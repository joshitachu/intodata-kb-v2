import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import TopologyPage from "@/app/topology/page";
import type { StreamState } from "@/hooks/useStreamMessage";

/**
 * The copilot wired to the real topology page: scope envelope, per-level
 * prompts, notices, chip navigation, and the invariant that no highlight path
 * ever touches the viewport.
 *
 * React Flow is replaced with a shim that records the nodes/edges the page
 * hands it and spies on every viewport method, because the emphasis under test
 * lives entirely in node `data.dimmed` / `data.highlighted` and edge opacity.
 * The heavy child canvases are stubbed to identifiable markers -- which of them
 * mounts is how "the chip navigated to the right level" is observed.
 */

const mocks = vi.hoisted(() => ({
  fitView: vi.fn(),
  fitBounds: vi.fn(() => Promise.resolve()),
  getViewport: vi.fn(() => ({ x: 0, y: 0, zoom: 1 })),
  setViewport: vi.fn(),
  zoomTo: vi.fn(),
  setCenter: vi.fn(),
  nodes: { current: [] as Array<{ id: string; data: Record<string, unknown> }> },
  edges: { current: [] as Array<{ id: string; style?: Record<string, unknown> }> },
  canvasProps: { current: {} as Record<string, Record<string, unknown>> },
  // The canvas's own gestures, so a test can drill without going through a chip.
  handlers: { current: {} as Record<string, ((event: unknown, node: unknown) => void) | undefined> },
  useProjectLandscape: vi.fn(),
  useJobHealth: vi.fn(),
  useFieldLineage: vi.fn(),
  useViewport: vi.fn(),
  useConversation: vi.fn(),
  useCreateConversation: vi.fn(),
  useUpdateConversation: vi.fn(),
  useStreamMessage: vi.fn(),
  // Typed as the tests read it back: every send from this page carries a scope.
  sendMessage: vi.fn(
    (
      _request: {
        query: string;
        page_scope: {
          surface: string;
          project_id: string;
          level: Record<string, unknown>;
          selected_entity?: { kind: string; label: string; entity_id?: string };
        };
      },
      _conversationId?: number,
    ) => Promise.resolve(),
  ),
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
      edges: Array<{ id: string; style?: Record<string, unknown> }>;
      children?: ReactNode;
      onNodeClick?: (event: unknown, node: unknown) => void;
      onNodeDoubleClick?: (event: unknown, node: unknown) => void;
    }) => {
      mocks.nodes.current = props.nodes;
      mocks.edges.current = props.edges;
      mocks.handlers.current = {
        onNodeClick: props.onNodeClick,
        onNodeDoubleClick: props.onNodeDoubleClick,
      };
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
  const component = (props: Record<string, unknown>) => {
    mocks.canvasProps.current[testId] = props;
    return createElement("div", { "data-testid": testId });
  };
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
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/topology",
}));

vi.mock("@/contexts/ChatContext", () => ({
  useChatContext: () => ({
    selectedProject: "",
    setSelectedProject: mocks.setSelectedProject,
  }),
}));

// The real LandscapeUnavailableError is kept: the page distinguishes a
// documented 404 from a service failure with `instanceof`.
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

function target(label: string, level: Record<string, unknown>) {
  return { label, level };
}

function visualTarget(
  label: string,
  level: Record<string, unknown>,
  visualRole: "primary" | "supporting" | "path",
  intent?: "navigate" | "reference",
) {
  return { label, level, visual_role: visualRole, ...(intent ? { intent } : {}) };
}

const GROUP_TARGET = target("Orders", { kind: "group", groupName: "Orders" });
const JOB_TARGET = target("Order sync", { kind: "job", jobName: "tJob_OrderSync" });
const COMPONENT_TARGET = target("The mapping", {
  kind: "component",
  jobName: "tJob_OrderSync",
  componentName: "tMap_1",
});
const MISSING_TARGET = target("Ghost job", { kind: "job", jobName: "tJob_NotInThisScan" });

function assistantMessage(targets: unknown[]) {
  return {
    id: 900,
    role: "assistant",
    content: "Orders is the group that feeds the warehouse.",
    sources: null,
    latency_ms: 120,
    status: "completed",
    created_at: "2026-08-20T10:00:00Z",
    structured_answer: {
      response_type: "agent_response_v1",
      kind: "answer",
      content: "Orders is the group that feeds the warehouse.",
      synthesis_source: "agent_plan",
      facts: [],
      artifact_links: [],
      evidence_refs: [],
      workflow_cards: [],
      stop_reason: "answered",
      factual_fingerprint: "a".repeat(64),
      topology_targets: targets,
    },
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

function toolCompletedEvent(targets: unknown[]) {
  return {
    event_id: "run-1:evt:000002",
    run_id: "run-1",
    sequence: 2,
    type: "tool.completed",
    created_at: "2026-08-20T10:00:00Z",
    public_payload: { summary: "Read the diagram", targets },
    replayable: true,
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

/**
 * A validated overlay -> business id space, one AFAS->Maximo flow. This is the
 * shape the reported "what flows from AFAS to Maximo" run was asked against.
 */
function validatedOverlayLandscape() {
  return {
    project_id: "werkorder",
    applications: [],
    systems: [],
    edges: [],
    channels: [],
    interfaces: [],
    diagnostics: [],
    unmapped: [],
    summary: {},
    business_topology: {
      systems: [
        { id: "sys-afas", name: "AFAS" },
        { id: "sys-maximo", name: "Maximo" },
      ],
      data_streams: [
        {
          id: "employees",
          name: "Employees",
          source_system_id: "sys-afas",
          destination_system_id: "sys-maximo",
          business_object_ids: [],
          integration_solution_ids: [],
          status: "confirmed",
          artifact_ids: [],
          evidence_ids: [],
        },
      ],
      integration_solutions: [],
    },
    topology_state: { validated: true, stale: false },
  };
}

/** A landscape whose overlay is present but unvalidated -> scanner id space. */
function staleOverlayLandscape() {
  return {
    project_id: "werkorder",
    applications: [],
    systems: [],
    edges: [],
    channels: [],
    interfaces: [],
    diagnostics: [],
    unmapped: [],
    summary: {},
    business_topology: { systems: [], data_streams: [], integration_solutions: [] },
    topology_state: { validated: false, stale: false },
  };
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <TopologyPage />
    </QueryClientProvider>,
  );
}

/** Waits for the projects fetch + graph fetch to settle into a laid-out canvas. */
async function renderSettled() {
  const result = renderPage();
  await waitFor(() => expect(mocks.nodes.current.length).toBeGreaterThan(0));
  return result;
}

function nodeById(id: string) {
  return mocks.nodes.current.find((node) => node.id === id);
}

/** Drill into a job the way the canvas does, so level changes need no chip. */
async function doubleClickJobNode(jobName: string) {
  await act(async () => {
    mocks.handlers.current.onNodeDoubleClick?.({}, { id: `job:${jobName}`, data: {} });
  });
}

/** The answer's chip section; `Orders` also names a GraphToolbar group chip. */
function answerChip(name: RegExp) {
  return within(screen.getByLabelText("On this page")).getByRole("button", { name });
}

function viewportSpies() {
  return [
    mocks.fitView,
    mocks.fitBounds,
    mocks.setViewport,
    mocks.zoomTo,
    mocks.setCenter,
  ];
}

function viewportCallCount() {
  return viewportSpies().reduce((total, spy) => total + spy.mock.calls.length, 0);
}

/**
 * The layout effect legitimately fits the view once per layout identity, via
 * requestAnimationFrame. Let those land before measuring, so the assertion is
 * about the highlight path only rather than about scheduling luck.
 */
async function settledViewportCallCount() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 60));
  });
  return viewportCallCount();
}

/** The panel's message list scrolls itself; jsdom has no scrollIntoView. */
function copilotInput() {
  return screen.getByPlaceholderText("Ask about this diagram...");
}

beforeEach(() => {
  window.localStorage.clear();
  mocks.canvasProps.current = {};
  Element.prototype.scrollIntoView = vi.fn();
  mocks.useProjectLandscape.mockReturnValue(
    // Default: a documented 404, so the legacy design canvas is what renders.
    landscapeQuery({ isError: true, error: new (class extends Error {
      readonly status = 404;
      constructor() {
        super("needs a rescan");
        this.name = "LandscapeUnavailableError";
      }
    })() }),
  );
  mocks.useJobHealth.mockReturnValue({
    data: undefined,
    isSuccess: false,
    dataUpdatedAt: 0,
  });
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

// ── the real LandscapeUnavailableError, for the 404 branch ──────────────────
// The class above is structurally identical but not the same constructor, so
// the page's `instanceof` check needs the real one for the 404 tests.
async function useRealUnavailableError() {
  const { LandscapeUnavailableError } = await import("@/hooks/useProjectLandscape");
  mocks.useProjectLandscape.mockReturnValue(
    landscapeQuery({ isError: true, error: new LandscapeUnavailableError() }),
  );
}

describe("topology page copilot -- scope envelope", () => {
  it("sends the surface, project and level in view with every message", async () => {
    await useRealUnavailableError();
    await renderSettled();

    fireEvent.change(copilotInput(), {
      target: { value: "Which systems feed this?" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));

    await waitFor(() => expect(mocks.sendMessage).toHaveBeenCalled());
    const createConversation = mocks.useCreateConversation.mock.results[0].value;
    expect(createConversation.mutateAsync).toHaveBeenCalledWith({ surface: "topology" });
    const [request] = mocks.sendMessage.mock.calls[0];
    expect(request.page_scope).toEqual({
      surface: "topology",
      project_id: "werkorder",
      level: { kind: "project" },
      filters: [
        { name: "search", value: "all" },
        { name: "artifact", value: "all" },
        { name: "connections", value: "shown" },
      ],
    });
  });

  it("sends the drilled-into level, camelCase and without an absent sourceRoot", async () => {
    await useRealUnavailableError();
    await renderSettled();

    await doubleClickJobNode("tJob_OrderSync");
    await screen.findByTestId("job-graph-canvas");

    fireEvent.change(copilotInput(), {
      target: { value: "What breaks if this fails?" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));

    await waitFor(() => expect(mocks.sendMessage).toHaveBeenCalled());
    const [request] = mocks.sendMessage.mock.calls[0];
    expect(request.page_scope.level).toEqual({ kind: "job", jobName: "tJob_OrderSync" });
    expect(Object.keys(request.page_scope.level)).not.toContain("sourceRoot");
  });

  it.each([
    ["application", { kind: "application", item: { id: "app:orders" } }],
    ["system", { kind: "system", item: { id: "system:maximo" } }],
    ["relationship", { kind: "relationship", source: "a", target: "b", streamKeys: [] }],
    ["edge", {
      kind: "edge",
      item: {
        source: "a",
        target: "b",
        source_kind: "system",
        target_kind: "system",
        dependency_type: "data",
        mechanism: "api",
        direction: "out",
        reliability: "unknown",
        confidence: "confirmed",
        evidence_count: 1,
        provenance: [],
      },
    }],
  ] as const)("reports a truthful %s integration selected_entity kind", async (expectedKind, selection) => {
    mocks.useProjectLandscape.mockReturnValue(
      landscapeQuery({ data: staleOverlayLandscape(), isSuccess: true }),
    );
    await renderSettled();

    await act(async () => {
      const onExplore = mocks.canvasProps.current["landscape-canvas"].onExplore as
        | ((value: unknown) => void)
        | undefined;
      onExplore?.(selection);
    });

    fireEvent.change(copilotInput(), { target: { value: "Explain this selection" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => expect(mocks.sendMessage).toHaveBeenCalled());
    const [request] = mocks.sendMessage.mock.calls[0];
    expect(request.page_scope.selected_entity?.kind).toBe(expectedKind);
  });
});

describe("topology page copilot -- suggestions", () => {
  it("offers landscape prompts at the root and job prompts after drilling", async () => {
    await useRealUnavailableError();
    await renderSettled();

    // Suggestions are the idle state, so this test keeps the thread empty.
    expect(screen.getByText(/Which systems feed the most data/)).toBeInTheDocument();

    await doubleClickJobNode("tJob_OrderSync");
    await screen.findByTestId("job-graph-canvas");

    expect(screen.getByText("Which jobs does tJob_OrderSync depend on?")).toBeInTheDocument();
    expect(screen.queryByText(/Which systems feed the most data/)).not.toBeInTheDocument();
  });
});

describe("topology page copilot -- notices", () => {
  it("reports a scanner-derived overlay when the business overlay is unvalidated", async () => {
    mocks.useProjectLandscape.mockReturnValue(
      landscapeQuery({ data: staleOverlayLandscape(), isSuccess: true }),
    );
    await renderSettled();

    expect(document.querySelector('[data-notice="stale-overlay"]')).not.toBeNull();
    expect(document.querySelector('[data-notice="missing-artifact"]')).toBeNull();
  });

  it("reports a missing artifact for a documented 404", async () => {
    await useRealUnavailableError();
    await renderSettled();

    expect(document.querySelector('[data-notice="missing-artifact"]')).not.toBeNull();
    expect(document.querySelector('[data-notice="service-unreachable"]')).toBeNull();
  });

  it("reports an unreachable service, and says the diagram is unaffected", async () => {
    mocks.useProjectLandscape.mockReturnValue(
      landscapeQuery({ isError: true, error: new Error("connect ECONNREFUSED") }),
    );
    renderPage();

    const notice = await waitFor(() => {
      const found = document.querySelector('[data-notice="service-unreachable"]');
      expect(found).not.toBeNull();
      return found!;
    });
    expect(notice.textContent).toMatch(/diagram on this page is unaffected/i);
  });
});

describe("topology page copilot -- chips", () => {
  it("navigates a resolvable job target to the job canvas", async () => {
    await useRealUnavailableError();
    mocks.useConversation.mockReturnValue({
      data: { id: 42, messages: [assistantMessage([JOB_TARGET])] },
    });
    await renderSettled();

    expect(screen.queryByTestId("job-graph-canvas")).toBeNull();
    await waitFor(() => expect(screen.getByLabelText("On this page")).toBeInTheDocument());
    fireEvent.click(answerChip(/Order sync/));
    expect(await screen.findByTestId("job-graph-canvas")).toBeInTheDocument();
  });

  it("navigates a component target through the job level in the same tick", async () => {
    await useRealUnavailableError();
    mocks.useConversation.mockReturnValue({
      data: { id: 42, messages: [assistantMessage([COMPONENT_TARGET])] },
    });
    await renderSettled();

    await waitFor(() => expect(screen.getByLabelText("On this page")).toBeInTheDocument());
    fireEvent.click(answerChip(/The mapping/));
    // Landing on the component canvas is only possible if descendToJob and
    // descendToComponent were both dispatched -- the latter is a no-op unless
    // a job level is already on top of the stack.
    expect(await screen.findByTestId("component-graph-canvas")).toBeInTheDocument();
  });

  it("renders an unresolvable target inert and navigates nowhere", async () => {
    await useRealUnavailableError();
    mocks.useConversation.mockReturnValue({
      data: { id: 42, messages: [assistantMessage([MISSING_TARGET])] },
    });
    await renderSettled();

    const chip = await waitFor(() => {
      const found = document.querySelector('[data-target-key="job::tJob_NotInThisScan"]');
      expect(found).not.toBeNull();
      return found as HTMLElement;
    });
    expect(chip.getAttribute("data-inert")).toBe("true");
    expect(chip.tagName).toBe("SPAN");
    expect(screen.queryByRole("button", { name: /Ghost job/ })).toBeNull();

    fireEvent.click(chip);
    expect(screen.queryByTestId("job-graph-canvas")).toBeNull();
    expect(nodeById("group:Orders")?.data.highlighted).toBe(false);
  });
});

describe("topology page copilot -- highlighting", () => {
  it("emphasises an answer's targets without moving the viewport", async () => {
    await useRealUnavailableError();
    mocks.useConversation.mockReturnValue({
      data: { id: 42, messages: [assistantMessage([GROUP_TARGET])] },
    });
    await renderSettled();

    await waitFor(() => expect(nodeById("group:Orders")?.data.highlighted).toBe(true));
    expect(nodeById("group:Logging")).toBeUndefined();

    // The layout's own initial fit is allowed; nothing on a highlight path is.
    const baseline = await settledViewportCallCount();

    fireEvent.mouseEnter(answerChip(/Orders/));
    await waitFor(() => expect(nodeById("group:Orders")?.data.highlighted).toBe(true));
    expect(await settledViewportCallCount()).toBe(baseline);

    fireEvent.mouseLeave(answerChip(/Orders/));
    await waitFor(() => expect(nodeById("group:Orders")?.data.highlighted).toBe(true));
    expect(await settledViewportCallCount()).toBe(baseline);
  });

  it("highlights from a tool.completed event before the answer completes", async () => {
    await useRealUnavailableError();
    mocks.useStreamMessage.mockReturnValue({
      streamState: streamState({
        isStreaming: true,
        content: "Looking at",
        agentEvents: [toolCompletedEvent([GROUP_TARGET])],
      }),
      sendMessage: mocks.sendMessage,
      reset: mocks.resetStream,
    });
    await renderSettled();

    // Still streaming, no persisted answer -- the emphasis comes from the event.
    await waitFor(() => expect(nodeById("group:Orders")?.data.highlighted).toBe(true));
    expect(screen.queryByLabelText("Found so far")).toBeNull();
    // The answer's own chip section is not there yet; this is the live pass.
    expect(screen.queryByLabelText("On this page")).toBeNull();

    // Nothing beyond the layout's initial fit: the canvas must not pan or zoom
    // while prose is still streaming.
    const baseline = await settledViewportCallCount();
    expect(await settledViewportCallCount()).toBe(baseline);
  });

  it("clears highlights and chips when the project changes", async () => {
    await useRealUnavailableError();
    mocks.useConversation.mockReturnValue({
      data: { id: 42, messages: [assistantMessage([GROUP_TARGET])] },
    });
    await renderSettled();
    await waitFor(() => expect(nodeById("group:Orders")?.data.highlighted).toBe(true));

    // A project switch drops the thread, so no chip can be re-derived against
    // the new project's graph.
    mocks.useConversation.mockReturnValue({ data: undefined });
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "alitalend" } });

    await waitFor(() => expect(nodeById("group:Logging")).toBeDefined());
    expect(screen.queryByLabelText("On this page")).toBeNull();
    for (const node of mocks.nodes.current) {
      if (node.id.startsWith("group-bg:")) continue;
      expect(node.data.highlighted).toBe(false);
      expect(node.data.dimmed).toBe(false);
    }
  });
});

describe("topology page copilot -- follow conversation", () => {
  it("defaults to Follow on desktop and focuses the first exact primary target", async () => {
    await useRealUnavailableError();
    mocks.useConversation.mockReturnValue({
      data: {
        id: 42,
        messages: [assistantMessage([
          visualTarget("Orders", { kind: "group", groupName: "Orders" }, "primary"),
          visualTarget("Order sync", { kind: "job", jobName: "tJob_OrderSync" }, "primary"),
        ])],
      },
    });
    await renderSettled();

    expect(screen.getByRole("button", { name: "Follow conversation enabled" }))
      .toBeInTheDocument();
    const banner = await screen.findByTestId("auto-navigation-banner");
    // Focused, not moved: the group was reframed on the canvas the user was
    // already on, and the navigation stack did not change.
    expect(banner).toHaveTextContent("Focused on Orders");
    expect(banner).not.toHaveTextContent("Moved to");
    expect(banner).toHaveTextContent("to follow the conversation");
    // The second primary remains an ordinary target, but cannot trigger a
    // second move or drill for this answer (and its job node is not mounted in
    // the collapsed overview).
    expect(screen.queryByTestId("job-graph-canvas")).toBeNull();
    expect(answerChip(/Order sync/)).toBeInTheDocument();
    await waitFor(() => expect(nodeById("group:Orders")?.data.assistantVisualRole).toBe("primary"));
  });

  it("Stay put preserves highlights and blocks an automatic primary move", async () => {
    window.localStorage.setItem("copilot:follow:topology", "stay");
    await useRealUnavailableError();
    mocks.useConversation.mockReturnValue({
      data: {
        id: 42,
        messages: [assistantMessage([
          visualTarget("Orders", { kind: "group", groupName: "Orders" }, "primary"),
        ])],
      },
    });
    await renderSettled();

    expect(screen.getByRole("button", { name: "Stay put enabled" })).toBeInTheDocument();
    await waitFor(() => expect(nodeById("group:Orders")?.data.highlighted).toBe(true));
    expect(nodeById("group:Orders")?.data.assistantVisualRole).toBe("primary");
    expect(screen.queryByTestId("auto-navigation-banner")).toBeNull();
  });

  it("honours an explicit navigate target even while Stay put is selected", async () => {
    window.localStorage.setItem("copilot:follow:topology", "stay");
    await useRealUnavailableError();
    mocks.useConversation.mockReturnValue({
      data: {
        id: 42,
        messages: [assistantMessage([
          visualTarget(
            "Order sync",
            { kind: "job", jobName: "tJob_OrderSync" },
            "supporting",
            "navigate",
          ),
        ])],
      },
    });
    await renderSettled();

    expect(await screen.findByTestId("job-graph-canvas")).toBeInTheDocument();
    expect(screen.getByTestId("auto-navigation-banner")).toHaveTextContent(
      "because you asked to be taken there",
    );
  });

  it("persists the Follow/Stay put preference across a remount", async () => {
    await useRealUnavailableError();
    const { unmount } = await renderSettled();
    fireEvent.click(screen.getByRole("button", { name: "Follow conversation enabled" }));
    expect(screen.getByRole("button", { name: "Stay put enabled" })).toBeInTheDocument();
    expect(window.localStorage.getItem("copilot:follow:topology")).toBe("stay");
    unmount();

    await renderSettled();
    expect(screen.getByRole("button", { name: "Stay put enabled" })).toBeInTheDocument();
  });
});

/**
 * The reported failure, end to end: the AFAS<->Maximo pair selected on the
 * canvas, an answer that marked AFAS `primary` and Maximo `path`, and Follow on.
 */
describe("topology page copilot -- relationship-shaped follow", () => {
  const RELATIONSHIP_ANSWER = [
    visualTarget("AFAS", { kind: "integration", selection: { kind: "system", systemId: "sys-afas" } }, "primary"),
    visualTarget("Maximo", { kind: "integration", selection: { kind: "system", systemId: "sys-maximo" } }, "path"),
    visualTarget("Order sync", { kind: "job", jobName: "tJob_OrderSync" }, "supporting"),
  ];

  /**
   * Mounts with an empty thread, lets the canvas report the pair the user
   * selected, and only then delivers the answer -- the real order of events, and
   * the only one in which the scope is known when the answer lands.
   */
  async function answerWithRelationshipSelected(
    targets: unknown[],
    selectedEntity: Record<string, unknown> = {
      kind: "relationship",
      label: "AFAS ↔ Maximo",
      entity_id: "pair:system:sys-afas<->system:sys-maximo",
    },
  ) {
    mocks.useProjectLandscape.mockReturnValue(
      landscapeQuery({ data: validatedOverlayLandscape(), isSuccess: true }),
    );
    // Empty thread first: an answer that lands before the canvas has reported
    // the selection would be consumed without ever seeing the scope, which is
    // not the order these events happen in.
    mocks.useConversation.mockReturnValue({ data: { id: 42, messages: [] } });
    await renderSettled();

    mocks.useConversation.mockReturnValue({
      data: { id: 42, messages: [assistantMessage(targets)] },
    });
    await act(async () => {
      const onContextChange = mocks.canvasProps.current["landscape-canvas"].onContextChange as
        | ((value: unknown) => void)
        | undefined;
      onContextChange?.({
        selectedEntity,
        activeDetailTab: "relationship",
        filters: [{ name: "query", value: "all" }],
      });
    });
  }

  function landscapeProps() {
    return mocks.canvasProps.current["landscape-canvas"] as {
      assistantEmphasis: { nodeRoles: Map<string, string>; edgeRoles: Map<string, string> };
      assistantFocusRequest: { nodeIds: string[]; edgeIds?: string[] } | null;
    };
  }

  it("follows the relationship, not the endpoint the answer called primary", async () => {
    await answerWithRelationshipSelected(RELATIONSHIP_ANSWER);

    const banner = await screen.findByTestId("auto-navigation-banner");
    expect(banner).toHaveTextContent("Focused on AFAS → Maximo");
    expect(banner).not.toHaveTextContent("Moved to");
    expect(banner.dataset.movement).toBe("focused");
  });

  it("leaves the navigation stack at the landscape", async () => {
    await answerWithRelationshipSelected(RELATIONSHIP_ANSWER);
    await screen.findByTestId("auto-navigation-banner");

    // Which canvas is mounted IS the navigation stack: descending to the
    // relationship would swap in the integration detail canvas.
    expect(screen.getByTestId("landscape-canvas")).toBeInTheDocument();
    expect(screen.queryByTestId("integration-canvas")).toBeNull();
    expect(screen.queryByTestId("job-graph-canvas")).toBeNull();
    // Undo restores a viewport, not a level -- there is no level to pop.
    fireEvent.click(screen.getByRole("button", { name: /undo/i }));
    expect(screen.getByTestId("landscape-canvas")).toBeInTheDocument();
  });

  it("emphasises both endpoints and the connecting edge, in the ids the canvas draws", async () => {
    await answerWithRelationshipSelected(RELATIONSHIP_ANSWER);
    await screen.findByTestId("auto-navigation-banner");

    const { assistantEmphasis, assistantFocusRequest } = landscapeProps();
    expect(assistantEmphasis.nodeRoles.get("sys-afas")).toBe("primary");
    expect(assistantEmphasis.nodeRoles.get("sys-maximo")).toBe("primary");
    expect(assistantEmphasis.nodeRoles.get("system:sys-afas")).toBe("primary");
    expect(assistantEmphasis.nodeRoles.get("system:sys-maximo")).toBe("primary");
    expect(assistantEmphasis.edgeRoles.get("pair:system:sys-afas<->system:sys-maximo"))
      .toBe("primary");

    expect(assistantFocusRequest?.nodeIds).toEqual(
      expect.arrayContaining(["sys-afas", "system:sys-afas", "sys-maximo", "system:sys-maximo"]),
    );
    expect(assistantFocusRequest?.edgeIds).toEqual(
      expect.arrayContaining(["pair:system:sys-afas<->system:sys-maximo"]),
    );
  });

  it("shows the relationship as the answer's first chip", async () => {
    await answerWithRelationshipSelected(RELATIONSHIP_ANSWER);
    await screen.findByTestId("auto-navigation-banner");

    const chips = within(screen.getByLabelText("On this page")).getAllByRole("button");
    expect(chips[0]).toHaveTextContent("AFAS → Maximo");
  });

  it("still lets an explicit navigate target win over the relationship", async () => {
    await answerWithRelationshipSelected([
      ...RELATIONSHIP_ANSWER,
      visualTarget("Order sync", { kind: "job", jobName: "tJob_OrderSync" }, "supporting", "navigate"),
    ]);

    expect(await screen.findByTestId("job-graph-canvas")).toBeInTheDocument();
    const banner = screen.getByTestId("auto-navigation-banner");
    expect(banner).toHaveTextContent("Moved to Order sync");
    expect(banner).toHaveTextContent("because you asked to be taken there");
  });

  it("keeps highlights but does not reframe the viewport under Stay put", async () => {
    window.localStorage.setItem("copilot:follow:topology", "stay");
    await answerWithRelationshipSelected(RELATIONSHIP_ANSWER);

    expect(screen.queryByTestId("auto-navigation-banner")).toBeNull();
    const { assistantEmphasis, assistantFocusRequest } = landscapeProps();
    expect(assistantEmphasis.nodeRoles.get("sys-afas")).toBe("primary");
    expect(assistantEmphasis.nodeRoles.get("sys-maximo")).toBe("primary");
    expect(assistantFocusRequest).toBeNull();
  });

  it("does not follow a lone system when the pair has no flow in this scan", async () => {
    await answerWithRelationshipSelected(
      [visualTarget(
        "AFAS",
        { kind: "integration", selection: { kind: "system", systemId: "sys-afas" } },
        "primary",
      )],
      {
        kind: "relationship",
        label: "AFAS ↔ Ghost",
        entity_id: "pair:system:sys-afas<->system:sys-ghost",
      },
    );

    expect(screen.queryByTestId("auto-navigation-banner")).toBeNull();
    // The highlight still lands: refusing to move is not refusing to answer.
    expect(landscapeProps().assistantEmphasis.nodeRoles.get("sys-afas")).toBe("primary");
  });
});

describe("topology page copilot -- panel state", () => {
  it("starts as the collapsed rail below desktop width", async () => {
    await useRealUnavailableError();
    mocks.useViewport.mockReturnValue({ isMobile: true, isTablet: false, isDesktop: false });
    await renderSettled();

    expect(screen.getByRole("button", { name: "Expand Assistant" })).toBeInTheDocument();
    expect(screen.queryByPlaceholderText("Ask about this diagram...")).toBeNull();
  });

  it("remembers the choice across a remount", async () => {
    await useRealUnavailableError();
    const { unmount } = await renderSettled();

    fireEvent.click(screen.getByRole("button", { name: "Collapse Assistant" }));
    expect(screen.getByRole("button", { name: "Expand Assistant" })).toBeInTheDocument();
    unmount();

    await renderSettled();
    expect(screen.getByRole("button", { name: "Expand Assistant" })).toBeInTheDocument();
  });
});

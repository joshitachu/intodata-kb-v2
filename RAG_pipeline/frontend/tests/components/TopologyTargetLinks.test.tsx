import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { TopologyTargetLinks } from "@/components/copilot/TopologyTargetLinks";
import { MessageBubble } from "@/components/MessageBubble";
import {
  parseTopologyConversationFromParams,
  parseTopologyLevelFromParams,
  parseTopologyProjectFromParams,
} from "@/lib/topologyUrl";
import type { MessageResponse } from "@/types";

/**
 * Topology targets on a page with no canvas. These were parsed and dropped
 * before, which made the most actionable part of a topology answer invisible
 * anywhere except the page that already showed it.
 *
 * The assertions read the level back OUT of each href through the same parser
 * the topology page uses, rather than string-matching a query string -- what
 * matters is that the link resolves to the intended level, not its spelling.
 */

vi.mock("@/hooks/useViewport", () => ({
  useViewport: () => ({ isMobile: false, isTablet: false, isDesktop: true }),
}));

function linkLevel(name: RegExp) {
  const href = screen.getByRole("link", { name }).getAttribute("href") ?? "";
  return new URLSearchParams(href.slice(href.indexOf("?") + 1));
}

const TARGETS = [
  { label: "Order sync", level: { kind: "job", jobName: "tJob_OrderSync" } },
  {
    label: "Werkorder mapping",
    level: {
      kind: "component",
      jobName: "tJob_OrderSync",
      componentName: "tMap_1",
      sourceRoot: "werkorder",
    },
  },
  {
    label: "Quintiq to Maximo",
    level: {
      kind: "integration",
      selection: {
        kind: "relationship",
        source: "landscape-system:aaaa1111",
        target: "landscape-system:bbbb2222",
        streamKeys: ["business-streamstream-1"],
      },
    },
  },
];

describe("TopologyTargetLinks", () => {
  it("renders nothing when the answer carries no targets", () => {
    const { container } = render(<TopologyTargetLinks payload={undefined} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing for a payload that is not a list of targets", () => {
    const { container } = render(<TopologyTargetLinks payload={{ nope: true }} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("points each link at the level its target names", () => {
    render(
      <TopologyTargetLinks payload={TARGETS} projectId="werkorder" conversationId={42} />,
    );

    expect(parseTopologyLevelFromParams(linkLevel(/Order sync/))).toEqual({
      kind: "job",
      jobName: "tJob_OrderSync",
    });
    expect(parseTopologyLevelFromParams(linkLevel(/Werkorder mapping/))).toEqual({
      kind: "component",
      jobName: "tJob_OrderSync",
      componentName: "tMap_1",
      sourceRoot: "werkorder",
    });
    expect(parseTopologyLevelFromParams(linkLevel(/Quintiq to Maximo/))).toEqual(
      TARGETS[2].level,
    );
  });

  it("carries the project and the thread so the link opens in context", () => {
    render(
      <TopologyTargetLinks payload={TARGETS} projectId="werkorder" conversationId={42} />,
    );
    const params = linkLevel(/Order sync/);
    expect(parseTopologyProjectFromParams(params)).toBe("werkorder");
    expect(parseTopologyConversationFromParams(params)).toBe(42);
  });

  it("drops a malformed target without taking the rest of the answer with it", () => {
    render(
      <TopologyTargetLinks
        payload={[{ label: "Broken", level: { kind: "job" } }, TARGETS[0]]}
        projectId="werkorder"
      />,
    );
    expect(screen.queryByRole("link", { name: /Broken/ })).toBeNull();
    expect(screen.getByRole("link", { name: /Order sync/ })).toBeInTheDocument();
  });
});

// ── inside a chat message ──────────────────────────────────────────────────

function assistantMessage(overrides: Partial<MessageResponse> = {}): MessageResponse {
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
      evidence_summary: {
        version: "1.0",
        evidence_step_count: 2,
        successful_sources: [
          { family: "topology", role: "structure", evidence_count: 1 },
          { family: "documentation", role: "explanation", evidence_count: 1 },
        ],
        failed_sources: [],
        zero_evidence: false,
        documentation: {
          provider_used: "kimi",
          fallback_used: false,
          attempts: [{ provider: "kimi", status: "succeeded" }],
        },
      },
      stop_reason: "answered",
      factual_fingerprint: "a".repeat(64),
      // Additive key: not declared on the strict schema, read off the raw
      // record (see readTopologyTargetsPayload).
      topology_targets: TARGETS,
    } as unknown as MessageResponse["structured_answer"],
    ...overrides,
  };
}

describe("MessageBubble topology links", () => {
  it("shows them on a surface that asked for them", () => {
    render(
      <MessageBubble
        message={assistantMessage()}
        conversationId={42}
        showTopologyLinks
        topologyProjectId="werkorder"
      />,
    );
    expect(parseTopologyProjectFromParams(linkLevel(/Order sync/))).toBe("werkorder");
  });

  it("keeps provenance, document citations, and topology links together", () => {
    render(
      <MessageBubble
        message={assistantMessage({
          sources: [
            {
              document_id: 7,
              filename: "03_DATA_FLOWS_AND_LINEAGE.md",
              score: 0.92,
              text: "The order sync sends confirmed work orders to Maximo.",
              reference_number: 1,
              chunk_indexes: [4],
              document_role: "source",
            },
          ],
        })}
        conversationId={42}
        showTopologyLinks
        topologyProjectId="werkorder"
      />,
    );

    expect(screen.getByLabelText("Answer evidence")).toHaveAttribute(
      "data-evidence-state",
      "fused",
    );
    expect(screen.getByRole("link", { name: /Order sync/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show 1 source" }));
    expect(screen.getByText("03_DATA_FLOWS_AND_LINEAGE.md")).toBeInTheDocument();
  });

  it("stays out of the way by default, so the docked copilot keeps its chips", () => {
    render(<MessageBubble message={assistantMessage()} conversationId={42} />);
    expect(screen.queryByRole("link", { name: /Order sync/ })).toBeNull();
  });

  it("prefers the project the question was asked about over the one selected now", () => {
    render(
      <MessageBubble
        message={assistantMessage({
          page_scope: {
            surface: "topology",
            project_id: "alitalend",
            level: { kind: "project" },
          },
        })}
        conversationId={42}
        showTopologyLinks
        topologyProjectId="werkorder"
      />,
    );
    expect(parseTopologyProjectFromParams(linkLevel(/Order sync/))).toBe("alitalend");
  });
});

import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useProgressiveTargets } from "@/components/copilot/useProgressiveTargets";
import {
  buildTopologyGraphData,
  type ResolvedTopologyTarget,
  type TopologyGraphData,
} from "@/lib/topologyTargets";
import type { ManifestData } from "@/lib/graphTransform";
import type { PublicAgentEvent } from "@/types";

const MANIFEST: ManifestData = {
  job_calls: { tJob_OrderSync: [] },
  job_called_by: {},
  job_connections_used: {},
  connection_used_by: {},
  job_groups: { tJob_OrderSync: "Orders" },
};

function graph(): TopologyGraphData {
  return buildTopologyGraphData({ manifest: MANIFEST });
}

function toolEvent(
  sequence: number,
  targets: unknown,
  overrides: Partial<PublicAgentEvent> = {},
): PublicAgentEvent {
  return {
    event_id: `evt-${sequence}`,
    run_id: "run-1",
    sequence,
    type: "tool.completed",
    created_at: "2026-08-20T10:00:00Z",
    public_payload: { tool_label: "get topology node", status: "ok", targets },
    replayable: true,
    ...overrides,
  };
}

interface HarnessProps {
  events: readonly PublicAgentEvent[];
  onHighlight: (targets: ResolvedTopologyTarget[]) => void;
}

function Harness({ events, onHighlight }: HarnessProps) {
  const resolutions = useProgressiveTargets({ events, graph: graph(), onHighlight });
  return <span data-testid="count">{resolutions.length}</span>;
}

describe("useProgressiveTargets", () => {
  it("highlights from tool events while the answer is still streaming", () => {
    const onHighlight = vi.fn();
    render(
      <Harness
        events={[toolEvent(1, [{ label: "tJob_OrderSync", level: { kind: "job", jobName: "tJob_OrderSync" } }])]}
        onHighlight={onHighlight}
      />,
    );

    // No final answer has arrived -- the canvas can already emphasise the node.
    expect(onHighlight).toHaveBeenCalledOnce();
    expect(onHighlight.mock.calls[0][0].map((item: ResolvedTopologyTarget) => item.key)).toEqual([
      "job::tJob_OrderSync",
    ]);
  });

  it("accumulates across events and does not re-fire for a repeated target", () => {
    const onHighlight = vi.fn();
    const first = toolEvent(1, [
      { label: "tJob_OrderSync", level: { kind: "job", jobName: "tJob_OrderSync" } },
    ]);
    const { rerender } = render(<Harness events={[first]} onHighlight={onHighlight} />);
    expect(onHighlight).toHaveBeenCalledTimes(1);

    rerender(
      <Harness
        events={[first, toolEvent(2, [{ label: "tJob_OrderSync", level: { kind: "job", jobName: "tJob_OrderSync" } }])]}
        onHighlight={onHighlight}
      />,
    );
    expect(onHighlight).toHaveBeenCalledTimes(1);

    rerender(
      <Harness
        events={[first, toolEvent(3, [{ label: "Orders", level: { kind: "group", groupName: "Orders" } }])]}
        onHighlight={onHighlight}
      />,
    );
    expect(onHighlight).toHaveBeenCalledTimes(2);
    expect(onHighlight.mock.calls[1][0]).toHaveLength(2);
  });

  it("never emits an unresolvable target for highlighting", () => {
    const onHighlight = vi.fn();
    const { getByTestId } = render(
      <Harness
        events={[toolEvent(1, [{ label: "Ghost", level: { kind: "job", jobName: "tJob_Missing" } }])]}
        onHighlight={onHighlight}
      />,
    );

    expect(onHighlight).not.toHaveBeenCalled();
    // Still returned to the panel, so it can render the chip inert.
    expect(getByTestId("count")).toHaveTextContent("1");
  });

  it("ignores a malformed targets payload instead of throwing", () => {
    const onHighlight = vi.fn();
    const { getByTestId } = render(
      <Harness events={[toolEvent(1, [{ label: "Bad" }, "nonsense"])]} onHighlight={onHighlight} />,
    );

    expect(onHighlight).not.toHaveBeenCalled();
    expect(getByTestId("count")).toHaveTextContent("0");
  });
});

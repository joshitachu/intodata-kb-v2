import { memo } from "react";
import { Handle, Position } from "@xyflow/react";
import { Layers, ChevronsRight } from "lucide-react";
import type { ChainSummaryNodeData } from "@/lib/chainCollapse";
import {
  CARD_SHADOW,
  DIMMED_STYLE,
  SUBTLE_TEXT_COLOR,
  cardShadow,
} from "@/lib/nodeStyles";

const SLATE_ACCENT = "#64748b";

function typeBreakdown(memberTypes: string[]): string {
  const counts = new Map<string, number>();
  for (const t of memberTypes) counts.set(t, (counts.get(t) ?? 0) + 1);
  const sorted = [...counts.entries()].sort((a, b) => b[1] - a[1]);
  const top = sorted.slice(0, 3).map(([type, count]) => `${count}× ${type}`);
  const rest = sorted.length - 3;
  return rest > 0 ? `${top.join(", ")} +${rest} more` : top.join(", ");
}

function ChainSummaryNodeComponent({ data }: { data: ChainSummaryNodeData }) {
  return (
    <div
      className="topo-node rounded-lg cursor-pointer"
      style={{
        background: "white",
        border: "1.5px dashed #cbd5e1",
        boxShadow: data.highlighted ? cardShadow(true, SLATE_ACCENT) : CARD_SHADOW,
        width: 220,
        padding: "8px 10px",
        ...(data.dimmed ? DIMMED_STYLE : { opacity: 1 }),
      }}
      title={`${data.count} unbranched components, collapsed for readability. Click to expand.`}
      data-testid="chain-summary-node"
    >
      <Handle type="target" position={Position.Left} className="!w-2 !h-2 !bg-slate-300 !border-2 !border-white" />

      <div className="flex items-center gap-1.5">
        <Layers className="h-3.5 w-3.5 shrink-0 text-slate-500" aria-hidden="true" />
        <span className="text-[11px] font-semibold text-slate-700">
          {data.count} components
        </span>
      </div>

      <div
        className="text-[9px] truncate mt-1"
        style={{ color: SUBTLE_TEXT_COLOR }}
        title={typeBreakdown(data.memberTypes)}
      >
        {typeBreakdown(data.memberTypes)}
      </div>

      <div
        className="flex items-center gap-1 mt-1 text-[9px] font-medium"
        style={{ color: SUBTLE_TEXT_COLOR }}
      >
        <ChevronsRight className="h-3 w-3 shrink-0" aria-hidden="true" />
        <span>Click to expand</span>
      </div>

      <Handle type="source" position={Position.Right} className="!w-2 !h-2 !bg-slate-300 !border-2 !border-white" />
    </div>
  );
}

export const ChainSummaryNode = memo(ChainSummaryNodeComponent);

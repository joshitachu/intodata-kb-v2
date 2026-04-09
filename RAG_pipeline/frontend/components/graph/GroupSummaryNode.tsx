import { memo } from "react";
import { Handle, Position } from "@xyflow/react";

export interface GroupSummaryNodeData {
  label: string;
  groupName: string;
  color: { bg: string; border: string; text: string };
  jobCount: number;
  directionBreakdown: { in: number; out: number; both: number; unknown: number };
  topSystems: string[];
  connectionCount: number;
  dimmed?: boolean;
  highlighted?: boolean;
}

function GroupSummaryNodeComponent({ data }: { data: GroupSummaryNodeData }) {
  const { directionBreakdown: dir } = data;

  return (
    <div
      className="topo-node topo-node-enter rounded-xl overflow-hidden cursor-pointer"
      style={{
        width: 240,
        opacity: data.dimmed ? 0.15 : 1,
        background: `linear-gradient(135deg, ${data.color.bg}, white 70%)`,
        boxShadow: data.highlighted
          ? `0 0 20px 4px ${data.color.border}50, 0 4px 20px rgba(0,0,0,0.12)`
          : `0 1px 3px rgba(0,0,0,0.08), 0 4px 12px rgba(0,0,0,0.04)`,
        border: `1.5px solid ${data.color.border}40`,
        transition: "opacity 0.3s ease, box-shadow 0.3s ease",
      }}
    >
      <Handle type="target" position={Position.Top} className="!w-2.5 !h-2.5 !bg-slate-300 !border-2 !border-white" />

      {/* Top accent bar with gradient */}
      <div
        style={{
          height: 3,
          background: `linear-gradient(90deg, ${data.color.border}, ${data.color.border}80, transparent)`,
        }}
      />

      <div className="px-3 py-2.5">
        {/* Header */}
        <div className="flex items-center justify-between mb-2">
          <span
            className="text-[13px] font-bold tracking-tight truncate"
            style={{ color: data.color.text }}
            title={data.label}
          >
            {data.label}
          </span>
          <span
            className="text-[10px] font-bold px-2 py-0.5 rounded-full shrink-0 ml-2"
            style={{
              background: `linear-gradient(135deg, ${data.color.border}, ${data.color.border}cc)`,
              color: "#fff",
              boxShadow: `0 1px 4px ${data.color.border}40`,
            }}
          >
            {data.jobCount}
          </span>
        </div>

        {/* Direction badges */}
        <div className="flex items-center gap-1 mb-2">
          {dir.in > 0 && (
            <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-md text-white"
              style={{ background: "linear-gradient(135deg, #10b981, #059669)" }}>
              {dir.in} IN
            </span>
          )}
          {dir.out > 0 && (
            <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-md text-white"
              style={{ background: "linear-gradient(135deg, #3b82f6, #2563eb)" }}>
              {dir.out} OUT
            </span>
          )}
          {dir.both > 0 && (
            <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-md text-white"
              style={{ background: "linear-gradient(135deg, #f59e0b, #d97706)" }}>
              {dir.both} BOTH
            </span>
          )}
          {data.connectionCount > 0 && (
            <span className="text-[9px] font-medium px-1.5 py-0.5 rounded-md text-purple-700 bg-purple-50 border border-purple-200">
              {data.connectionCount} conn
            </span>
          )}
        </div>

        {/* External systems */}
        {data.topSystems.length > 0 && (
          <div
            className="text-[9px] text-muted-foreground truncate leading-tight"
            title={data.topSystems.join(", ")}
          >
            {data.topSystems.slice(0, 3).join(" · ")}
            {data.topSystems.length > 3 && ` +${data.topSystems.length - 3}`}
          </div>
        )}
      </div>

      <Handle type="source" position={Position.Bottom} className="!w-2.5 !h-2.5 !bg-slate-300 !border-2 !border-white" />
    </div>
  );
}

export const GroupSummaryNode = memo(GroupSummaryNodeComponent);

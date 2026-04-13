import { memo } from "react";
import { Handle, Position } from "@xyflow/react";

interface JobNodeData {
  label: string;
  group: string;
  color: { bg: string; border: string; text: string };
  callsOut: number;
  calledBy: number;
  connCount: number;
  purpose: string;
  componentCount: number;
  hasDocs: boolean;
  externalSystems: string[];
  summary: string;
  direction: string;
  dimmed?: boolean;
  highlighted?: boolean;
  compact?: boolean;
}

const DIRECTION_BADGE: Record<string, { label: string; gradient: string }> = {
  OnRamp: { label: "IN", gradient: "linear-gradient(135deg, #10b981, #059669)" },
  OffRamp: { label: "OUT", gradient: "linear-gradient(135deg, #3b82f6, #2563eb)" },
  Bidirectional: { label: "BOTH", gradient: "linear-gradient(135deg, #f59e0b, #d97706)" },
};

function JobNodeComponent({ data }: { data: JobNodeData }) {
  const badge = data.direction ? DIRECTION_BADGE[data.direction] : undefined;

  return (
    <div
      className="topo-node rounded-lg cursor-pointer"
      style={{
        background: `linear-gradient(135deg, ${data.color.bg}, white 80%)`,
        border: `1.5px solid ${data.color.border}50`,
        opacity: data.dimmed ? 0.12 : 1,
        boxShadow: data.highlighted
          ? `0 0 16px 3px ${data.color.border}50, 0 2px 8px rgba(0,0,0,0.1)`
          : `0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04)`,
        maxWidth: 200,
        padding: "6px 10px",
      }}
    >
      <Handle type="target" position={Position.Top} className="!w-2 !h-2 !bg-slate-300 !border-2 !border-white" />
      <div className="flex items-center gap-1.5">
        {badge && (
          <span
            className="text-[8px] font-bold px-1.5 py-0.5 rounded text-white shrink-0"
            style={{ background: badge.gradient, boxShadow: "0 1px 2px rgba(0,0,0,0.15)" }}
          >
            {badge.label}
          </span>
        )}
        <span
          className="text-[11px] font-semibold truncate"
          style={{ color: data.color.text }}
          title={data.label}
        >
          {data.label}
        </span>
      </div>
      <Handle type="source" position={Position.Bottom} className="!w-2 !h-2 !bg-slate-300 !border-2 !border-white" />
    </div>
  );
}

export const JobNode = memo(JobNodeComponent);

import { memo } from "react";
import { Handle, Position } from "@xyflow/react";

interface ConnectionNodeData {
  label: string;
  connectionType: string;
  host: string;
  database: string;
  usageCount: number;
  dimmed?: boolean;
  highlighted?: boolean;
  compact?: boolean;
}

function ConnectionNodeComponent({ data }: { data: ConnectionNodeData }) {
  return (
    <div
      className="topo-node topo-node-enter rounded-lg cursor-default"
      style={{
        background: "linear-gradient(135deg, #faf5ff, white 80%)",
        border: "1.5px solid rgba(168, 85, 247, 0.3)",
        opacity: data.dimmed ? 0.12 : 1,
        boxShadow: data.highlighted
          ? "0 0 16px 3px rgba(168, 85, 247, 0.3), 0 2px 8px rgba(0,0,0,0.1)"
          : "0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04)",
        maxWidth: 160,
        padding: "6px 10px",
        transition: "opacity 0.3s ease, box-shadow 0.3s ease",
      }}
    >
      <Handle type="target" position={Position.Top} className="!w-2 !h-2 !bg-purple-300 !border-2 !border-white" />
      <div className="flex items-center gap-1.5">
        <div className="w-1.5 h-1.5 rounded-full bg-purple-400 shrink-0" />
        <span
          className="text-[11px] font-semibold text-purple-900 truncate"
          title={data.label}
        >
          {data.label}
        </span>
      </div>
      <Handle type="source" position={Position.Bottom} className="!w-2 !h-2 !bg-purple-300 !border-2 !border-white" />
    </div>
  );
}

export const ConnectionNode = memo(ConnectionNodeComponent);

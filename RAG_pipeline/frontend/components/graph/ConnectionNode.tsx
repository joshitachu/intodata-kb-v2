import { memo } from "react";
import { Handle, Position } from "@xyflow/react";
import { useZoomBelow } from "@/hooks/useZoomBelow";
import {
  CARD_BORDER,
  CARD_SHADOW,
  DIMMED_STYLE,
  LABEL_MIN_ZOOM,
  TITLE_COLOR,
  cardShadow,
} from "@/lib/nodeStyles";

const ACCENT = "#a855f7"; // violet-500 — connection identity

interface ConnectionNodeData {
  label: string;
  // Prefix-shortened display name (see graphTransform.groupCommonPrefix).
  displayLabel?: string;
  // Canvas layout direction: handles swap sides in LR mode.
  dir?: "TB" | "LR";
  connectionType: string;
  host: string;
  database: string;
  usageCount: number;
  dimmed?: boolean;
  highlighted?: boolean;
  compact?: boolean;
}

function ConnectionNodeComponent({ data }: { data: ConnectionNodeData }) {
  const zoomedOut = useZoomBelow(LABEL_MIN_ZOOM);
  const pillMode = data.compact === true && zoomedOut;
  const horizontal = data.dir === "LR";
  const targetPosition = horizontal ? Position.Left : Position.Top;
  const sourcePosition = horizontal ? Position.Right : Position.Bottom;

  if (pillMode) {
    return (
      <div
        className="topo-node rounded-md relative"
        style={{
          background: "#a855f726",
          border: "1.5px solid #a855f7",
          boxShadow: CARD_SHADOW,
          width: 120,
          height: 32,
          ...(data.dimmed ? DIMMED_STYLE : { opacity: 1 }),
        }}
        title={data.label}
      >
        <Handle type="target" position={targetPosition} className="!w-2 !h-2 !bg-purple-300 !border-2 !border-white" />
        <Handle type="source" position={sourcePosition} className="!w-2 !h-2 !bg-purple-300 !border-2 !border-white" />
      </div>
    );
  }

  return (
    <div
      className="topo-node rounded-lg cursor-default relative"
      style={{
        background: "white",
        border: CARD_BORDER,
        boxShadow: cardShadow(data.highlighted === true, ACCENT),
        maxWidth: 160,
        padding: "6px 10px",
        ...(data.dimmed ? DIMMED_STYLE : { opacity: 1 }),
      }}
    >
      <Handle type="target" position={targetPosition} className="!w-2 !h-2 !bg-purple-300 !border-2 !border-white" />
      <div className="flex items-center gap-1.5">
        <div className="w-1.5 h-1.5 rounded-full bg-purple-400 shrink-0" />
        <span
          className="text-[11px] font-semibold truncate"
          style={{ color: TITLE_COLOR }}
          title={data.label}
        >
          {data.displayLabel ?? data.label}
        </span>
      </div>
      <Handle type="source" position={sourcePosition} className="!w-2 !h-2 !bg-purple-300 !border-2 !border-white" />
    </div>
  );
}

export const ConnectionNode = memo(ConnectionNodeComponent);

import { memo } from "react";
import { Handle, Position } from "@xyflow/react";
import type { EffectiveStatus } from "@/lib/healthPropagation";
import { STATUS_META, isOverlayStatus } from "@/lib/healthStatusMeta";
import {
  CARD_BORDER,
  CARD_SHADOW,
  DIMMED_STYLE,
  SUBTLE_TEXT_COLOR,
  TITLE_COLOR,
  accentBarStyle,
  cardShadow,
} from "@/lib/nodeStyles";

export interface GroupSummaryNodeData {
  label: string;
  groupName: string;
  // Canvas layout direction: handles swap sides in LR mode.
  dir?: "TB" | "LR";
  color: { bg: string; border: string; text: string };
  jobCount: number;
  routeCount?: number;
  talendJobCount?: number;
  directionBreakdown: { in: number; out: number; both: number; unknown: number };
  topSystems: string[];
  connectionCount: number;
  dimmed?: boolean;
  highlighted?: boolean;
  // Runtime-health overlay (S3): the worst effective status across members.
  worstStatus?: EffectiveStatus;
}

const DIRECTION_BADGE = {
  in: { label: "IN", color: "#10b981" },
  out: { label: "OUT", color: "#3b82f6" },
  both: { label: "BOTH", color: "#f59e0b" },
} as const;

function GroupSummaryNodeComponent({ data }: { data: GroupSummaryNodeData }) {
  const { directionBreakdown: dir } = data;

  const showStatus = isOverlayStatus(data.worstStatus);
  const meta = showStatus ? STATUS_META[data.worstStatus!] : undefined;
  const statusTitle = meta
    ? `Worst runtime status in group: ${meta.label} — ${meta.description}`
    : "";

  const accent = data.color.border;
  const horizontal = data.dir === "LR";

  return (
    <div
      className="topo-node rounded-xl overflow-hidden cursor-pointer relative"
      style={{
        width: 240,
        background: "white",
        border: meta ? `2px solid ${meta.color}` : CARD_BORDER,
        boxShadow: meta ? CARD_SHADOW : cardShadow(data.highlighted === true, accent),
        ...(data.dimmed ? DIMMED_STYLE : { opacity: 1 }),
      }}
    >
      <Handle type="target" position={horizontal ? Position.Left : Position.Top} className="!w-2.5 !h-2.5 !bg-slate-300 !border-2 !border-white" />
      <span style={accentBarStyle(accent, 12)} />

      {meta && (
        <span
          data-testid="group-status-badge"
          className="absolute top-1.5 right-1.5 z-10 flex items-center gap-0.5 rounded-full px-1.5 py-0.5 text-[9px] font-bold text-white shadow"
          style={{ background: meta.color }}
          title={statusTitle}
          aria-label={statusTitle}
        >
          <span className="inline-block h-1.5 w-1.5 rounded-full bg-white/90" />
          {meta.short}
        </span>
      )}

      <div className="px-3 py-2.5">
        {/* Header */}
        <div className="flex items-center justify-between mb-2">
          <span
            className="text-[13px] font-bold tracking-tight truncate"
            style={{ color: TITLE_COLOR }}
            title={data.label}
          >
            {data.label}
          </span>
          <span
            className="text-[10px] font-bold px-2 py-0.5 rounded-full shrink-0 ml-2"
            style={{
              background: `${accent}14`,
              color: accent,
              border: `1px solid ${accent}30`,
            }}
          >
            {data.jobCount}
          </span>
        </div>

        {(data.routeCount || data.talendJobCount) ? (
          <div className="mb-2 flex items-center gap-1 text-[9px] font-semibold">
            {(data.talendJobCount ?? 0) > 0 && (
              <span className="rounded bg-blue-50 px-1.5 py-0.5 text-blue-700">{data.talendJobCount} Jobs</span>
            )}
            {(data.routeCount ?? 0) > 0 && (
              <span className="rounded bg-orange-50 px-1.5 py-0.5 text-orange-700">{data.routeCount} Routes</span>
            )}
          </div>
        ) : null}

        {/* Direction badges */}
        <div className="flex items-center gap-1 mb-2">
          {dir.in > 0 && (
            <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-md text-white"
              style={{ background: DIRECTION_BADGE.in.color }}>
              {dir.in} IN
            </span>
          )}
          {dir.out > 0 && (
            <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-md text-white"
              style={{ background: DIRECTION_BADGE.out.color }}>
              {dir.out} OUT
            </span>
          )}
          {dir.both > 0 && (
            <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-md text-white"
              style={{ background: DIRECTION_BADGE.both.color }}>
              {dir.both} BOTH
            </span>
          )}
          {data.connectionCount > 0 && (
            <span className="text-[9px] font-medium px-1.5 py-0.5 rounded-md text-slate-600 bg-slate-50 border border-slate-200">
              {data.connectionCount} conn
            </span>
          )}
        </div>

        {/* External systems */}
        {data.topSystems.length > 0 && (
          <div
            className="text-[9px] truncate leading-tight"
            style={{ color: SUBTLE_TEXT_COLOR }}
            title={data.topSystems.join(", ")}
          >
            {data.topSystems.slice(0, 3).join(" · ")}
            {data.topSystems.length > 3 && ` +${data.topSystems.length - 3}`}
          </div>
        )}
      </div>

      <Handle type="source" position={horizontal ? Position.Right : Position.Bottom} className="!w-2.5 !h-2.5 !bg-slate-300 !border-2 !border-white" />
    </div>
  );
}

export const GroupSummaryNode = memo(GroupSummaryNodeComponent);

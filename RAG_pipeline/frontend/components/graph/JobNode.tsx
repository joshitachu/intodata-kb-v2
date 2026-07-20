import { memo } from "react";
import { Handle, Position } from "@xyflow/react";
import type { EffectiveStatus } from "@/lib/healthPropagation";
import type { JobStatus } from "@/lib/jobHealth";
import { STATUS_META, isOverlayStatus } from "@/lib/healthStatusMeta";

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
  // Runtime-health overlay (S3). Present only while the overlay is active and
  // this job matched a runtime task; absent/unknown => rendered unchanged.
  ownStatus?: JobStatus;
  effectiveStatus?: EffectiveStatus;
  impactedVia?: string[];
  runtimeTaskCount?: number;
}

const DIRECTION_BADGE: Record<string, { label: string; gradient: string }> = {
  OnRamp: { label: "IN", gradient: "linear-gradient(135deg, #10b981, #059669)" },
  OffRamp: { label: "OUT", gradient: "linear-gradient(135deg, #3b82f6, #2563eb)" },
  Bidirectional: { label: "BOTH", gradient: "linear-gradient(135deg, #f59e0b, #d97706)" },
};

function JobNodeComponent({ data }: { data: JobNodeData }) {
  const badge = data.direction ? DIRECTION_BADGE[data.direction] : undefined;

  const showStatus = isOverlayStatus(data.effectiveStatus);
  const meta = showStatus ? STATUS_META[data.effectiveStatus!] : undefined;

  const border = meta
    ? `2px solid ${meta.color}`
    : `1.5px solid ${data.color.border}50`;
  const boxShadow = meta
    ? `0 0 0 2px ${meta.color}44, 0 0 12px 2px ${meta.color}33`
    : data.highlighted
      ? `0 0 16px 3px ${data.color.border}50, 0 2px 8px rgba(0,0,0,0.1)`
      : `0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04)`;

  // Full accessible description for the status (own + how it was impacted).
  let statusTitle = "";
  if (meta) {
    statusTitle = `Runtime status: ${meta.label} — ${meta.description}`;
    if (data.effectiveStatus === "impacted" && data.impactedVia && data.impactedVia.length > 0) {
      statusTitle += ` (via ${data.impactedVia.join(" → ")}, ${data.impactedVia.length} hop${data.impactedVia.length > 1 ? "s" : ""})`;
    }
  }

  return (
    <div
      className="topo-node rounded-lg cursor-pointer relative"
      style={{
        background: `linear-gradient(135deg, ${data.color.bg}, white 80%)`,
        border,
        opacity: data.dimmed ? 0.12 : 1,
        boxShadow,
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
      {meta && (
        <span
          data-testid="job-status-badge"
          className="absolute -top-2 -right-1.5 flex items-center gap-0.5 rounded-full px-1.5 py-0.5 text-[8px] font-bold text-white shadow"
          style={{ background: meta.color }}
          title={statusTitle}
          aria-label={statusTitle}
        >
          <span
            data-testid="job-status-dot"
            className="inline-block h-1.5 w-1.5 rounded-full bg-white/90"
          />
          {meta.short}
        </span>
      )}
      <Handle type="source" position={Position.Bottom} className="!w-2 !h-2 !bg-slate-300 !border-2 !border-white" />
    </div>
  );
}

export const JobNode = memo(JobNodeComponent);

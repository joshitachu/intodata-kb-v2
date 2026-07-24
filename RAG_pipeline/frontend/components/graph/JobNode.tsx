import { memo } from "react";
import { Handle, Position } from "@xyflow/react";
import { Crosshair, ChevronUp, ChevronDown, Route, Cog } from "lucide-react";
import type { EffectiveStatus } from "@/lib/healthPropagation";
import type { JobStatus } from "@/lib/jobHealth";
import { STATUS_META, isOverlayStatus } from "@/lib/healthStatusMeta";
import { useFocusInteraction } from "@/components/graph/focusInteraction";
import { useZoomBelow } from "@/hooks/useZoomBelow";
import {
  CARD_BORDER,
  CARD_SHADOW,
  DIMMED_STYLE,
  LABEL_MIN_ZOOM,
  TITLE_COLOR,
  accentBarStyle,
  cardShadow,
  middleEllipsis,
} from "@/lib/nodeStyles";
import { JOB_NODE_WIDTH, JOB_NODE_WIDTH_COMPACT, JOB_NODE_HEIGHT_COMPACT } from "@/lib/graphTransform";

interface JobNodeData {
  label: string;
  // Prefix-shortened display name (see graphTransform.groupCommonPrefix).
  // Rendered on the card; `label` keeps the full name for identity/tooltips.
  displayLabel?: string;
  // Canvas layout direction: handles swap sides so LR edges enter/exit
  // horizontally instead of elbowing into the card's top/bottom.
  dir?: "TB" | "LR";
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
  artifactKind?: string;
  artifactRole?: string;
  dimmed?: boolean;
  highlighted?: boolean;
  compact?: boolean;
  // Runtime-health overlay (S3). Present only while the overlay is active and
  // this job matched a runtime task; absent/unknown => rendered unchanged.
  ownStatus?: JobStatus;
  effectiveStatus?: EffectiveStatus;
  impactedVia?: string[];
  runtimeTaskCount?: number;
  // Focus mode. Set only on nodes rendered inside a focus neighbourhood.
  focusFocal?: boolean;
  focusUpstream?: number;
  focusDownstream?: number;
}

const DIRECTION_BADGE: Record<string, { label: string; color: string }> = {
  OnRamp: { label: "IN", color: "#10b981" },
  OffRamp: { label: "OUT", color: "#3b82f6" },
  Bidirectional: { label: "BOTH", color: "#f59e0b" },
};

function JobNodeComponent({ data }: { data: JobNodeData }) {
  const badge = data.direction ? DIRECTION_BADGE[data.direction] : undefined;
  const focus = useFocusInteraction();
  // Zoom-adaptive label: zoomed far out, compact nodes render as text-free
  // pills (same 150x36 footprint, so dagre layout is never invalidated).
  const zoomedOut = useZoomBelow(LABEL_MIN_ZOOM);
  const pillMode = data.compact === true && zoomedOut;

  const showStatus = isOverlayStatus(data.effectiveStatus);
  const meta = showStatus ? STATUS_META[data.effectiveStatus!] : undefined;

  // While focus mode is active, this node is the focal point iff flagged so.
  const isFocal = data.focusFocal === true;
  const upstreamHidden = data.focusUpstream ?? 0;
  const downstreamHidden = data.focusDownstream ?? 0;
  const isFocusActive = focus?.active === true;

  const accent = data.color.border;
  const isRoute = data.artifactKind === "route";
  const artifactAccent = isRoute ? "#ea580c" : "#2563eb";
  const horizontal = data.dir === "LR";
  const targetPosition = horizontal ? Position.Left : Position.Top;
  const sourcePosition = horizontal ? Position.Right : Position.Bottom;
  const border = isFocal
    ? `2px solid ${artifactAccent}`
    : meta
      ? `2px solid ${meta.color}`
      : `1px solid ${isRoute ? "#fed7aa" : "#bfdbfe"}`;
  const boxShadow =
    data.highlighted && !meta
      ? cardShadow(true, artifactAccent)
      : CARD_SHADOW;

  // Full accessible description for the status (own + how it was impacted).
  let statusTitle = "";
  if (meta) {
    statusTitle = `Runtime status: ${meta.label} — ${meta.description}`;
    if (data.effectiveStatus === "impacted" && data.impactedVia && data.impactedVia.length > 0) {
      statusTitle += ` (via ${data.impactedVia.join(" → ")}, ${data.impactedVia.length} hop${data.impactedVia.length > 1 ? "s" : ""})`;
    }
  }

  const statusBadge = meta && (
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
  );

  // Pill mode: unreadably small text carries no information — render the node
  // as a group-colored chip instead, so the zoomed-out canvas reads as a map
  // of colored group clusters (matching the legend) rather than blank boxes.
  // Health status still overrides the chip border so a DOWN job stands out.
  if (pillMode) {
    return (
      <div
        className="topo-node rounded-md relative"
        style={{
          background: meta ? "white" : `${artifactAccent}20`,
          border: meta ? `2px solid ${meta.color}` : `1.5px solid ${artifactAccent}`,
          boxShadow,
          width: JOB_NODE_WIDTH_COMPACT,
          height: JOB_NODE_HEIGHT_COMPACT,
          ...(data.dimmed ? DIMMED_STYLE : { opacity: 1 }),
        }}
        title={data.label}
      >
        <Handle type="target" position={targetPosition} className="!w-2 !h-2 !bg-slate-300 !border-2 !border-white" />
        {statusBadge}
        <Handle type="source" position={sourcePosition} className="!w-2 !h-2 !bg-slate-300 !border-2 !border-white" />
      </div>
    );
  }

  return (
    <div
      className="topo-node group rounded-lg cursor-pointer relative"
      style={{
        background: "white",
        border,
        boxShadow,
        maxWidth: data.compact ? JOB_NODE_WIDTH_COMPACT : JOB_NODE_WIDTH,
        padding: "6px 10px 6px 12px",
        ...(data.dimmed ? DIMMED_STYLE : { opacity: 1 }),
      }}
    >
      <Handle type="target" position={targetPosition} className="!w-2 !h-2 !bg-slate-300 !border-2 !border-white" />
      <span style={accentBarStyle(artifactAccent)} />
      <span
        className="absolute right-2 top-1 flex items-center gap-1 rounded px-1.5 py-0.5 text-[8px] font-bold"
        style={{ background: `${artifactAccent}18`, color: artifactAccent }}
        aria-label={isRoute ? "Talend Route" : "Talend Job"}
      >
        {isRoute ? <Route className="h-2.5 w-2.5" /> : <Cog className="h-2.5 w-2.5" />}
        {isRoute ? "ROUTE" : "JOB"}
      </span>

      {/* Focus trigger: appears on hover (or always while focal). Single-click
          enters/re-centres focus mode on this job; clicking the focal node's
          button again exits. stopPropagation keeps ReactFlow's node click
          (detail panel) and double-click (drill-down) untouched. */}
      {focus && (
        <button
          type="button"
          data-testid="job-focus-button"
          onClick={(e) => {
            e.stopPropagation();
            focus.onFocusJob(data.label);
          }}
          onDoubleClick={(e) => e.stopPropagation()}
          className={`absolute -top-2 -left-2 z-10 flex h-5 w-5 items-center justify-center rounded-full border border-white text-white shadow transition-opacity duration-150 ${
            isFocal ? "opacity-100" : "opacity-0 group-hover:opacity-100"
          }`}
          style={{ background: isFocal ? accent : `${accent}cc` }}
          title={isFocal ? "Exit focus mode" : `Focus on ${data.label} and its neighbourhood`}
          aria-label={isFocal ? "Exit focus mode" : `Focus on ${data.label}`}
        >
          <Crosshair className="h-3 w-3" />
        </button>
      )}

      {/* Boundary expand affordances (focus mode only): this job sits on the
          horizon and still has hidden callers/callees. */}
      {isFocusActive && upstreamHidden > 0 && (
        <button
          type="button"
          data-testid="focus-expand-upstream"
          onClick={(e) => {
            e.stopPropagation();
            focus?.onExpandHops();
          }}
          onDoubleClick={(e) => e.stopPropagation()}
          className="absolute -top-2.5 left-1/2 z-10 flex -translate-x-1/2 items-center gap-0.5 rounded-full bg-slate-800 px-1.5 py-0.5 text-[8px] font-bold text-white shadow"
          title={`Show ${upstreamHidden} more upstream (expand focus)`}
        >
          <ChevronUp className="h-2.5 w-2.5" />+{upstreamHidden}
        </button>
      )}
      {isFocusActive && downstreamHidden > 0 && (
        <button
          type="button"
          data-testid="focus-expand-downstream"
          onClick={(e) => {
            e.stopPropagation();
            focus?.onExpandHops();
          }}
          onDoubleClick={(e) => e.stopPropagation()}
          className="absolute -bottom-2.5 left-1/2 z-10 flex -translate-x-1/2 items-center gap-0.5 rounded-full bg-slate-800 px-1.5 py-0.5 text-[8px] font-bold text-white shadow"
          title={`Show ${downstreamHidden} more downstream (expand focus)`}
        >
          <ChevronDown className="h-2.5 w-2.5" />+{downstreamHidden}
        </button>
      )}
      <div className="flex items-center gap-1.5">
        {badge && (
          <span
            className="text-[8px] font-bold px-1.5 py-0.5 rounded text-white shrink-0"
            style={{ background: badge.color }}
          >
            {badge.label}
          </span>
        )}
        <span
          className="text-[11px] font-semibold truncate"
          style={{ color: TITLE_COLOR }}
          title={data.label}
        >
          {middleEllipsis(data.displayLabel ?? data.label, (data.compact ? 20 : 26) - (badge ? 6 : 0))}
        </span>
      </div>
      {statusBadge}
      <Handle type="source" position={sourcePosition} className="!w-2 !h-2 !bg-slate-300 !border-2 !border-white" />
    </div>
  );
}

export const JobNode = memo(JobNodeComponent);

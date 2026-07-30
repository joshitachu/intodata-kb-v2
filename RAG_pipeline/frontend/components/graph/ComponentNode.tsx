import { memo } from "react";
import { Handle, Position } from "@xyflow/react";
import { Code2, Layers, ArrowUpRight } from "lucide-react";
import type { ComponentNodeData } from "@/lib/jobGraphTransform";
import {
  CARD_BORDER,
  CARD_SHADOW,
  DIMMED_STYLE,
  SUBTLE_TEXT_COLOR,
  TITLE_COLOR,
  accentBarStyle,
  cardShadow,
} from "@/lib/nodeStyles";

const ROLE_LABEL: Record<string, string> = {
  input: "IN",
  transform: "XFORM",
  output: "OUT",
  orchestration: "ORCH",
  logging: "LOG",
};

function ComponentNodeComponent({ data }: { data: ComponentNodeData }) {
  const drillable = data.hasSubgraph || data.subjobTarget !== null;
  const technicalName = data.technicalName || data.label;

  // Prefer the plain-English description as the hover tooltip when the
  // generated docs supplied one; otherwise keep the prior behaviour (the
  // drill hint for drillable nodes, else the raw component id).
  const explanation = data.description || data.typeExplanation;
  const tooltip = explanation
    ? `${data.label}: ${explanation}`
    : drillable
      ? "Double-click to open"
      : data.label;

  return (
    <div
      className="topo-node rounded-lg cursor-pointer relative"
      style={{
        background: "white",
        border: drillable
          ? `1.5px dashed ${data.color.border}90`
          : CARD_BORDER,
        boxShadow: cardShadow(data.highlighted === true, data.color.border),
        width: 220,
        padding: "8px 10px 8px 12px",
        ...(data.dimmed ? DIMMED_STYLE : { opacity: 1 }),
        ...(data.role === "logging" && !data.highlighted ? { filter: "saturate(0.65)" } : {}),
      }}
      title={tooltip}
    >
      <Handle
        type="target"
        position={Position.Left}
        className="!w-2 !h-2 !bg-slate-300 !border-2 !border-white"
      />
      <span style={accentBarStyle(data.color.border)} />

      <div className="flex items-center gap-1.5">
        <span
          className="text-[8px] font-bold px-1.5 py-0.5 rounded text-white shrink-0"
          style={{ background: data.color.border }}
        >
          {ROLE_LABEL[data.role] ?? data.role.toUpperCase()}
        </span>
        <span
          className="text-[11px] font-semibold truncate"
          style={{ color: TITLE_COLOR }}
          title={data.label}
        >
          {data.label}
        </span>
        {data.hasCode && (
          <Code2
            className="h-3 w-3 shrink-0 text-slate-400"
            aria-label="Has custom code"
            data-testid="has-code-indicator"
          />
        )}
        {data.hasSubgraph && (
          <Layers
            className="h-3 w-3 shrink-0"
            style={{ color: data.color.border }}
            aria-label="Drillable: has inner mapping graph"
            data-testid="subgraph-indicator"
          />
        )}
      </div>

      <div className="flex items-center gap-1.5 mt-1">
        <span
          className="text-[9px] text-slate-500 truncate"
          title={`${technicalName} · ${data.componentType}`}
        >
          {technicalName !== data.label && (
            <>
              <span>{technicalName}</span>
              <span aria-hidden="true"> · </span>
            </>
          )}
          <span>{data.componentType}</span>
        </span>
        {data.connectionRef && (
          <span
            className="text-[8px] font-medium px-1.5 py-0.5 rounded-full bg-purple-100 text-purple-800 truncate shrink-0 max-w-[90px]"
            title={`Connection: ${data.connectionRef}`}
            data-testid="connector-badge"
          >
            {data.connectionRef}
          </span>
        )}
        {(data.inputTableCount > 0 || data.outputTableCount > 0) && (
          <span
            className="text-[8px] font-medium px-1.5 py-0.5 rounded-full bg-amber-100 text-amber-800 shrink-0"
            title={`${data.inputTableCount} input / ${data.outputTableCount} output tables`}
            data-testid="tmap-tables-badge"
          >
            {data.inputTableCount}&rarr;{data.outputTableCount}
          </span>
        )}
      </div>

      {explanation && (
        <p
          className="mt-1 text-[9px] leading-snug text-slate-500 line-clamp-2"
          title={explanation}
          data-testid="component-description"
        >
          {explanation}
        </p>
      )}

      {data.subjobTarget && (
        <div
          className="flex items-center gap-1 mt-1 text-[9px] font-semibold"
          style={{ color: data.color.text }}
          data-testid="subjob-portal"
        >
          <ArrowUpRight className="h-3 w-3 shrink-0" />
          <span className="truncate" title={`Calls job ${data.subjobTarget}`}>
            {data.subjobTarget}
          </span>
        </div>
      )}

      <Handle
        type="source"
        position={Position.Right}
        className="!w-2 !h-2 !bg-slate-300 !border-2 !border-white"
      />
    </div>
  );
}

export const ComponentNode = memo(ComponentNodeComponent);

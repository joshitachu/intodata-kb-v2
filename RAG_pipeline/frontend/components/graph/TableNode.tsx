import { memo } from "react";
import { Handle, Position } from "@xyflow/react";
import {
  columnHandleId,
  TABLE_HEADER_HEIGHT,
  TABLE_ROW_HEIGHT,
  TABLE_SOURCE_HANDLE,
  type TableKind,
  type TableNodeData,
} from "@/lib/componentGraphTransform";
import { confidenceStyle } from "@/lib/lineageHighlight";
import {
  CARD_BORDER,
  DIMMED_STYLE,
  TITLE_COLOR,
  accentBarStyle,
  cardShadow,
} from "@/lib/nodeStyles";

const KIND_LABEL: Record<TableKind, string> = {
  input: "IN",
  var: "VAR",
  output: "OUT",
};

const KIND_COLOR: Record<TableKind, { bg: string; border: string; text: string }> = {
  input: { bg: "#dcfce7", border: "#22c55e", text: "#166534" },
  var: { bg: "#fef3c7", border: "#f59e0b", text: "#92400e" },
  output: { bg: "#dbeafe", border: "#3b82f6", text: "#1e40af" },
};

// Level-3 table node: one input/Var/output table. Var/output columns get a
// distinct target+source handle pair per row (standard schema-visualization
// pattern), ready for exact column-to-column edges once S4 resolves a real
// source column. Input tables have no captured columns yet (see
// types/componentGraph.ts), so they - and Var, which can itself be a
// source for output columns - additionally expose one table-level handle
// used for the honest table-to-column fan-out edges componentGraphTransform
// draws pre-S4.
function TableNodeComponent({ data }: { data: TableNodeData }) {
  const color = KIND_COLOR[data.kind];
  const canBeSource = data.kind !== "output";

  return (
    <div
      className="topo-node rounded-lg relative"
      style={{
        width: 220,
        background: "white",
        border: CARD_BORDER,
        boxShadow: cardShadow(data.highlighted === true, color.border),
        ...(data.dimmed ? DIMMED_STYLE : { opacity: 1 }),
      }}
    >
      <span style={accentBarStyle(color.border)} />
      <div
        className="relative flex items-center gap-1.5 px-2 rounded-t-lg"
        style={{ height: TABLE_HEADER_HEIGHT }}
        title={data.sourceComponent ? `From ${data.sourceComponent}` : data.tableName}
      >
        {canBeSource && (
          <Handle
            type="source"
            id={TABLE_SOURCE_HANDLE}
            position={Position.Right}
            className="!w-1.5 !h-1.5 !bg-slate-400 !border !border-white"
            data-testid="table-source-handle"
          />
        )}
        <span
          className="text-[8px] font-bold px-1 py-0.5 rounded text-white shrink-0"
          style={{ background: color.border }}
        >
          {KIND_LABEL[data.kind]}
        </span>
        <span
          className="text-[11px] font-semibold truncate"
          style={{ color: TITLE_COLOR }}
        >
          {data.tableName}
        </span>
      </div>

      {data.columns.length === 0 && (
        <div
          className="text-[10px] text-slate-400 italic px-2"
          style={{ height: TABLE_ROW_HEIGHT, display: "flex", alignItems: "center" }}
          data-testid="table-node-empty"
        >
          No columns captured
        </div>
      )}

      {data.columns.map((col, i) => {
        const top = TABLE_HEADER_HEIGHT + i * TABLE_ROW_HEIGHT + TABLE_ROW_HEIGHT / 2;
        const isActive = data.lineageActiveColumn === col.name;
        const isTouched = data.lineageTouchedColumns?.has(col.name) ?? false;
        const unresolvedConfidence = data.lineageUnresolvedColumns?.get(col.name);
        // Confidence is per-EDGE (a column can be fed by several hops of
        // different confidence), so a touched-but-resolved column gets a
        // neutral "on path" indicator rather than a specific (and possibly
        // wrong) confidence color - only the unresolved case has one true
        // confidence value to show (its single dynamic/opaque hop).
        const unresolvedStyle = unresolvedConfidence ? confidenceStyle(unresolvedConfidence) : null;
        const dotColor = unresolvedStyle?.color ?? "#6366f1";
        const clickable = typeof data.onColumnClick === "function";

        return (
          <div
            key={col.name}
            role={clickable ? "button" : undefined}
            tabIndex={clickable ? 0 : undefined}
            onClick={clickable ? () => data.onColumnClick?.(col.name) : undefined}
            onKeyDown={
              clickable
                ? (e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      data.onColumnClick?.(col.name);
                    }
                  }
                : undefined
            }
            className={`flex items-center px-2 text-[10px] border-t border-slate-100 truncate ${
              clickable ? "cursor-pointer hover:bg-slate-50" : ""
            }`}
            style={{
              height: TABLE_ROW_HEIGHT,
              background: isActive ? "#eef2ff" : isTouched ? `${dotColor}14` : undefined,
              fontWeight: isActive || isTouched ? 700 : undefined,
            }}
            title={col.expression ?? col.name}
            data-testid={`column-row-${col.name}`}
            data-lineage-active={isActive || undefined}
            data-lineage-touched={isTouched || undefined}
          >
            <Handle
              type="target"
              id={columnHandleId("target", col.name)}
              position={Position.Left}
              style={{ top }}
              className="!w-1.5 !h-1.5 !bg-slate-300 !border !border-white"
            />
            {(isTouched || isActive) && (
              <span
                className="w-1.5 h-1.5 rounded-full mr-1 shrink-0"
                style={{ background: dotColor }}
                aria-hidden="true"
                data-testid={`lineage-dot-${col.name}`}
              />
            )}
            <span className="truncate font-mono">{col.name}</span>
            {unresolvedStyle && (
              <span
                className="ml-1 text-[8px] font-bold px-1 rounded shrink-0 text-white"
                style={{ background: unresolvedStyle.color }}
                title={unresolvedStyle.label}
                data-testid={`lineage-badge-${col.name}`}
              >
                {unresolvedConfidence}
              </span>
            )}
            <Handle
              type="source"
              id={columnHandleId("source", col.name)}
              position={Position.Right}
              style={{ top }}
              className="!w-1.5 !h-1.5 !bg-slate-300 !border !border-white"
            />
          </div>
        );
      })}
    </div>
  );
}

export const TableNode = memo(TableNodeComponent);

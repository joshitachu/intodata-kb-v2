import { EdgeLabelRenderer, getBezierPath, type EdgeProps } from "@xyflow/react";

// Level-3 lineage overlay edge (S5): a real, resolved column-to-column
// hop from the field-lineage BFS, confidence-styled per
// lib/lineageHighlight.ts (color/dash already computed into `style`/
// `markerEnd` at edge-creation time by buildLineageOverlayEdges - this
// component only renders them plus a floating confidence badge, since
// color alone is not an accessible way to convey confidence).
export function LineageEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  style,
  markerEnd,
  data,
}: EdgeProps) {
  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  });

  const expression = data && typeof data.expression === "string" ? data.expression : "";
  const confidenceLabel =
    data && typeof data.confidenceLabel === "string" ? data.confidenceLabel : "";
  const strokeColor =
    style && typeof style.stroke === "string" ? style.stroke : "#94a3b8";

  return (
    <>
      <path id={id} d={edgePath} className="react-flow__edge-path" style={style} markerEnd={markerEnd} fill="none">
        <title>{confidenceLabel ? `${expression} [${confidenceLabel}]` : expression}</title>
      </path>
      <EdgeLabelRenderer>
        <div
          data-testid="lineage-edge-badge"
          style={{
            position: "absolute",
            transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
            background: strokeColor,
            color: "white",
            fontSize: 9,
            fontWeight: 600,
            padding: "1px 5px",
            borderRadius: 6,
            whiteSpace: "nowrap",
            pointerEvents: "none",
          }}
        >
          {confidenceLabel}
        </div>
      </EdgeLabelRenderer>
    </>
  );
}

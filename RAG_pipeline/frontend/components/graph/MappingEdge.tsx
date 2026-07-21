import { getBezierPath, type EdgeProps } from "@xyflow/react";

// Level-3 column-mapping edge: renders the raw expression as a native SVG
// <title> (nested inside the path, not via BaseEdge which doesn't accept
// children) so hovering the edge shows it as a browser tooltip - no extra
// hover-state wiring, and testable via getByTitle.
export function MappingEdge({
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
  const [edgePath] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  });
  const expression =
    data && typeof data.expression === "string" ? data.expression : "";

  return (
    <path
      id={id}
      d={edgePath}
      className="react-flow__edge-path"
      style={style}
      markerEnd={markerEnd}
      fill="none"
    >
      <title>{expression}</title>
    </path>
  );
}

import type { Edge } from "@xyflow/react";
import { tableNodeId, columnHandleId, TABLE_SOURCE_HANDLE } from "@/lib/componentGraphTransform";
import {
  LINEAGE_RESOLVED_CONFIDENCE_VALUES,
  type LineageFieldEdge,
  type LineageFieldResponse,
} from "@/types/lineage";

// Pure "heritage mode" highlight/dim logic (S5). No React, no fetching -
// takes an already-loaded LineageFieldResponse and the viewer's current
// context, and returns which ids to highlight at whichever level is
// currently visible. Kept separate from the canvases themselves so it is
// trivially Vitest-covered in isolation (root CLAUDE.md rule 11: prefer a
// pure-function module over threading this logic through component state).
//
// Honesty constraint that shapes every function here: the backend does not
// stitch lineage across job boundaries yet (lineage_service.py - "a single
// query is always scoped to one job_name"), and LineageFieldEdge carries a
// single `job_name`, not separate source/target job fields - there is no
// cross-job edge shape in the wire contract to render. Every function below
// therefore only ever highlights within `response.job_name`; asking it to
// highlight a different job/component than the one the data covers returns
// an empty result rather than fabricating a path the API never returned.

// ── Confidence visual encoding ───────────────────────────────────────
//
// certain/likely are real static references (solid); dynamic/opaque are
// unresolved (dashed/dotted) - never styled to look resolved, per the D2
// decision ("dynamic refs are first-class unresolved entries, rendered as
// such") and root CLAUDE.md rule against presenting uncertain data as fact.

export interface ConfidenceStyle {
  readonly color: string;
  readonly dashArray: string | undefined;
  readonly label: string;
}

const CONFIDENCE_STYLES: Record<string, ConfidenceStyle> = {
  certain: { color: "#16a34a", dashArray: undefined, label: "Certain" },
  likely: { color: "#3b82f6", dashArray: undefined, label: "Likely" },
  dynamic: { color: "#f59e0b", dashArray: "5 3", label: "Dynamic (unresolved)" },
  opaque: { color: "#94a3b8", dashArray: "2 3", label: "Opaque (unresolved)" },
};

const DEFAULT_CONFIDENCE_STYLE: ConfidenceStyle = CONFIDENCE_STYLES.opaque;

export function confidenceStyle(confidence: string): ConfidenceStyle {
  return CONFIDENCE_STYLES[confidence] ?? DEFAULT_CONFIDENCE_STYLE;
}

export function isResolvedConfidence(confidence: string): boolean {
  return LINEAGE_RESOLVED_CONFIDENCE_VALUES.includes(confidence);
}

// ── Shared helpers ───────────────────────────────────────────────────

export function lineageColumnKey(table: string, column: string): string {
  return `${table}::${column}`;
}

function transformationExpression(edge: LineageFieldEdge): string {
  return edge.transformations
    .map((t) => t.expression)
    .filter((expr) => expr.length > 0)
    .join(" -> ");
}

// ── Level-3 (component table graph) highlight ───────────────────────
//
// Scoped strictly to one component's own tables, because table names are
// only unique within a component (every tMap has its own "Var" table) -
// LineageFieldNode.component is the disambiguator, resolved server-side.

export interface LineageOverlayEdge {
  id: string;
  sourceTableId: string;
  sourceColumn: string;
  targetTableId: string;
  targetColumn: string;
  confidence: string;
  resolved: boolean;
  dynamicRefs: string[];
  expression: string;
}

export interface TableGraphLineageHighlight {
  // tableNodeId(...) values for tables touched by the path within this
  // component - everything else on the canvas should dim.
  tableIds: Set<string>;
  // `${table}::${column}` keys for the specific columns on the path.
  columnKeys: Set<string>;
  // Real column-to-column edges to draw atop the existing (structural)
  // mapping edges - only for resolved hops, which alone have a real source.
  overlayEdges: LineageOverlayEdge[];
  // Columns fed by an unresolved (dynamic/opaque) reference - no source
  // anchor exists, so these render a badge instead of a phantom edge.
  unresolvedColumns: Map<string, string>;
}

export function componentLineageHighlight(
  response: LineageFieldResponse,
  componentName: string,
): TableGraphLineageHighlight {
  const tableIds = new Set<string>();
  const columnKeys = new Set<string>();
  const unresolvedColumns = new Map<string, string>();

  if (!componentName || response.job_name === "") {
    return { tableIds, columnKeys, overlayEdges: [], unresolvedColumns };
  }

  for (const node of response.nodes) {
    if (node.component !== componentName) continue;
    tableIds.add(tableNodeId(node.table));
    columnKeys.add(lineageColumnKey(node.table, node.column));
  }

  const overlayEdges: LineageOverlayEdge[] = [];
  for (const edge of response.edges) {
    const targetInComponent = edge.target_component === componentName;
    const sourceInComponent = edge.source_component === componentName;

    if (!edge.resolved || !sourceInComponent || !targetInComponent) {
      // Either genuinely unresolved (constant/dynamic - no source to draw)
      // or the hop crosses to a different component within the same job,
      // which this level-3 canvas (one component's tables) cannot render -
      // it belongs on the level-2 job graph instead (jobLineageHighlight).
      if (targetInComponent && !isResolvedConfidence(edge.confidence)) {
        unresolvedColumns.set(
          lineageColumnKey(edge.target_table, edge.target_column),
          edge.confidence,
        );
      }
      continue;
    }

    overlayEdges.push({
      id: `lineage:${edge.source_table}.${edge.source_column}->${edge.target_table}.${edge.target_column}`,
      sourceTableId: tableNodeId(edge.source_table),
      sourceColumn: edge.source_column,
      targetTableId: tableNodeId(edge.target_table),
      targetColumn: edge.target_column,
      confidence: edge.confidence,
      resolved: edge.resolved,
      dynamicRefs: edge.dynamic_refs,
      expression: transformationExpression(edge),
    });
  }

  return { tableIds, columnKeys, overlayEdges, unresolvedColumns };
}

function splitColumnKey(key: string): [string, string] {
  const idx = key.indexOf("::");
  return idx === -1 ? [key, ""] : [key.slice(0, idx), key.slice(idx + 2)];
}

// Projects the (component-wide) highlight down to the columns that belong
// to one specific table, for merging into that table's own TableNodeData.
export function tableLineageProjection(
  highlight: TableGraphLineageHighlight,
  tableName: string,
): { touchedColumns: Set<string>; unresolvedColumns: Map<string, string> } {
  const touchedColumns = new Set<string>();
  for (const key of highlight.columnKeys) {
    const [table, column] = splitColumnKey(key);
    if (table === tableName) touchedColumns.add(column);
  }
  const unresolvedColumns = new Map<string, string>();
  for (const [key, confidence] of highlight.unresolvedColumns) {
    const [table, column] = splitColumnKey(key);
    if (table === tableName) unresolvedColumns.set(column, confidence);
  }
  return { touchedColumns, unresolvedColumns };
}

// React Flow edge type name for the lineage overlay (registered by
// ComponentGraphCanvas alongside the existing "mappingEdge" type).
export const LINEAGE_EDGE_TYPE = "lineageEdge";

// Builds real, confidence-styled column-to-column React Flow edges from a
// component's overlay hops. Source anchoring always uses the table-level
// TABLE_SOURCE_HANDLE rather than a per-column source handle: input tables
// never carry captured columns (component_graph_service.py - the scanner
// does not extract input-table schemas), so a per-column source handle may
// not exist even though the lineage entry names a real source column: the
// table-level handle is the only anchor guaranteed to exist on every
// possible source (input table or Var), mirroring how
// componentGraphTransform's own structural fan-out edges anchor sources
// (see candidateSourceTables). The target side always has captured columns
// (Var/output tables are always populated from tmap_data), so it anchors to
// the exact target column handle.
export function buildLineageOverlayEdges(highlight: TableGraphLineageHighlight): Edge[] {
  return highlight.overlayEdges.map((edge) => {
    const style = confidenceStyle(edge.confidence);
    return {
      id: edge.id,
      source: edge.sourceTableId,
      sourceHandle: TABLE_SOURCE_HANDLE,
      target: edge.targetTableId,
      targetHandle: columnHandleId("target", edge.targetColumn),
      type: LINEAGE_EDGE_TYPE,
      zIndex: 10,
      data: {
        expression: edge.expression,
        confidence: edge.confidence,
        confidenceLabel: style.label,
        dynamicRefs: edge.dynamicRefs,
      },
      style: { stroke: style.color, strokeWidth: 2.5, strokeDasharray: style.dashArray },
      markerEnd: { type: "arrowclosed" as const, color: style.color, width: 14, height: 14 },
    };
  });
}

// ── Level-2 (job component graph) highlight ─────────────────────────
//
// Intra-job hops that cross component boundaries (e.g. tMap_1's output feeds
// tFileOutputDelimited_1) render here as highlighted component nodes -
// level 3 only ever shows one component's own tables.

export function jobLineageHighlight(
  response: LineageFieldResponse,
  jobName: string,
): Set<string> {
  const ids = new Set<string>();
  // Never highlight a job's components from a response scoped to a
  // different job - would fabricate a cross-job hop the API never sent.
  if (response.job_name !== jobName) return ids;

  for (const node of response.nodes) {
    if (node.component) ids.add(`comp:${node.component}`);
  }
  for (const edge of response.edges) {
    if (edge.source_component) ids.add(`comp:${edge.source_component}`);
    if (edge.target_component) ids.add(`comp:${edge.target_component}`);
  }
  return ids;
}

// ── Level-1 (project job graph) highlight ───────────────────────────
//
// Job-boundary hops would render here once the backend stitches cross-job
// edges (architecture doc S2.2 step 5 / S5 backend increment). Today every
// response is scoped to exactly one job, so this always resolves to that
// single job node - correct and forward-compatible: it degrades to "the one
// job we have data for" rather than inventing neighbors.

export function projectLineageHighlight(response: LineageFieldResponse): Set<string> {
  return new Set([`job:${response.job_name}`]);
}

// ── Summary (for the path banner / legend) ──────────────────────────

export interface LineageSummary {
  jobName: string;
  startTable: string;
  startColumn: string;
  hopCount: number;
  confidenceCounts: Record<string, number>;
  dynamicRefCount: number;
  truncated: boolean;
}

export function summarizeLineage(response: LineageFieldResponse): LineageSummary {
  const confidenceCounts: Record<string, number> = {};
  let dynamicRefCount = 0;

  for (const edge of response.edges) {
    confidenceCounts[edge.confidence] = (confidenceCounts[edge.confidence] ?? 0) + 1;
    dynamicRefCount += edge.dynamic_refs.length;
  }

  return {
    jobName: response.job_name,
    startTable: response.start.table,
    startColumn: response.start.column,
    hopCount: response.edges.length,
    confidenceCounts,
    dynamicRefCount,
    truncated: response.truncated,
  };
}

// Re-exported for callers that only need the id-shaping helpers alongside
// the highlight functions above.
export { tableNodeId, columnHandleId };

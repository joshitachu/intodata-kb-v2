import dagre from "@dagrejs/dagre";
import type { Node, Edge } from "@xyflow/react";
import {
  RESOLVED_CONFIDENCE_VALUES,
  type ComponentGraphEdge,
  type ComponentGraphResponse,
  type ComponentGraphTable,
} from "@/types/componentGraph";

// ── Node data shape consumed by TableNode ───────────────────────────

export type TableKind = "input" | "var" | "output";

export interface TableColumnData {
  name: string;
  // Raw expression(s) that define this column, joined from the API's
  // edges (matched by exact table_name + column_name) - null when no
  // mapping entry targets this column (input-table columns: always null,
  // since the scanner never captures their entries - see types/componentGraph.ts).
  expression: string | null;
}

export interface TableNodeData {
  tableName: string;
  kind: TableKind;
  sourceComponent: string | null;
  columns: TableColumnData[];
  dimmed?: boolean;
  highlighted?: boolean;
  // S5 field-lineage overlay (lib/lineageHighlight.ts) - all optional and
  // absent whenever no lineage selection is active, so existing callers
  // (and TableNode.test.tsx's fixtures) are unaffected.
  //
  // Columns on the currently-highlighted path within this table.
  lineageTouchedColumns?: Set<string>;
  // Columns fed by an unresolved (dynamic/opaque) reference -> confidence,
  // for a badge instead of a phantom edge (no real source to draw).
  lineageUnresolvedColumns?: Map<string, string>;
  // The exact column the user clicked to start the trace, if it lives on
  // this table - gets a distinct "active" marker from the rest of the path.
  lineageActiveColumn?: string | null;
  assistantHighlightedColumns?: Set<string>;
  assistantColumnRoles?: ReadonlyMap<string, string>;
  assistantVisualRole?: string;
  // Invoked with the clicked column's name; wired up one level above
  // (ComponentGraphCanvas) to a field-lineage fetch.
  onColumnClick?: (columnName: string) => void;
  [key: string]: unknown;
}

export interface ComponentFlowResult {
  nodes: Node[];
  edges: Edge[];
  stats: {
    tableCount: number;
    columnCount: number;
    // Raw API edges (one per tmap entry) - the number of distinct mappings,
    // independent of how many candidate-source lines each fans out to.
    mappingCount: number;
    // Flow edges actually drawn (mappingCount fanned out across every
    // structurally possible source table).
    drawnEdgeCount: number;
    unresolvedMappingCount: number;
  };
}

export function tableNodeId(tableName: string): string {
  return `table:${tableName}`;
}

// Per-column handles so column-to-column mapping edges (once S4 resolves a
// specific source column) attach to the exact row - the standard
// schema-visualization pattern (architecture doc, section 2.1).
export function columnHandleId(
  direction: "source" | "target",
  columnName: string,
): string {
  return `${direction}:${columnName}`;
}

// One additional handle per table (not per column): the only anchor
// available for input tables, whose columns the scanner never captures,
// and the fan-out anchor used for every candidate source below - honest
// given the real data has no resolved source column pre-S4.
export const TABLE_SOURCE_HANDLE = "table-source";

function isResolved(confidence: string): boolean {
  return RESOLVED_CONFIDENCE_VALUES.includes(confidence);
}

function tableToNode(
  table: ComponentGraphTable,
  kind: TableKind,
  expressionIndex: Map<string, string[]>,
): Node {
  const data: TableNodeData = {
    tableName: table.table_name,
    kind,
    sourceComponent: table.source_component,
    columns: table.columns.map((name) => {
      const matches = expressionIndex.get(`${table.table_name}::${name}`);
      return { name, expression: matches ? matches.join(" | ") : null };
    }),
  };
  return {
    id: tableNodeId(table.table_name),
    type: "tableNode",
    position: { x: 0, y: 0 },
    data,
  };
}

// Structural (never expression-parsing) candidate-source resolution: which
// tables COULD feed this mapping, given only the tMap's own declared
// structure. Var-table entries can only originate from input tables (Var is
// populated from inputs); output-table entries can originate from input
// tables OR Var (tMap output expressions commonly chain through Var). This
// never inspects the expression text itself - doing so would duplicate the
// S4 deterministic tokenizer's job ad hoc in the frontend (see D2/D4 in the
// architecture doc and root CLAUDE.md rule 9 - no workarounds that
// pre-empt or fork a fix that belongs in one place).
function candidateSourceTables(
  mapping: ComponentGraphEdge,
  inputTableNames: string[],
  varTableName: string,
): string[] {
  if (mapping.target_table === varTableName) {
    return inputTableNames;
  }
  return [...inputTableNames, varTableName];
}

export function componentGraphToFlow(
  graph: ComponentGraphResponse,
): ComponentFlowResult {
  const expressionIndex = new Map<string, string[]>();
  for (const mapping of graph.edges) {
    const key = `${mapping.target_table}::${mapping.target_column}`;
    const list = expressionIndex.get(key) ?? [];
    list.push(mapping.expression);
    expressionIndex.set(key, list);
  }

  const nodes: Node[] = [
    ...graph.input_tables.map((t) => tableToNode(t, "input", expressionIndex)),
    tableToNode(graph.var_table, "var", expressionIndex),
    ...graph.output_tables.map((t) => tableToNode(t, "output", expressionIndex)),
  ];
  const knownIds = new Set(nodes.map((n) => n.id));

  const inputTableNames = graph.input_tables.map((t) => t.table_name);
  const varTableName = graph.var_table.table_name;

  const edges: Edge[] = [];
  let unresolvedMappingCount = 0;

  for (const mapping of graph.edges) {
    const resolved = isResolved(mapping.confidence);
    if (!resolved) unresolvedMappingCount++;

    const candidates = candidateSourceTables(mapping, inputTableNames, varTableName);
    const targetId = tableNodeId(mapping.target_table);
    if (!knownIds.has(targetId)) continue;

    for (const candidateTable of candidates) {
      const sourceId = tableNodeId(candidateTable);
      // Skip candidates absent from this response instead of crashing the
      // layout - mirrors jobGraphToFlow's dangling-edge guard.
      if (!knownIds.has(sourceId)) continue;

      edges.push({
        id: `map:${candidateTable}->${mapping.target_table}.${mapping.target_column}`,
        source: sourceId,
        sourceHandle: TABLE_SOURCE_HANDLE,
        target: targetId,
        targetHandle: columnHandleId("target", mapping.target_column),
        type: "mappingEdge",
        data: {
          expression: mapping.expression,
          resolvedRefs: mapping.resolved_refs,
          confidence: mapping.confidence,
        },
        style: resolved
          ? { stroke: "#3b82f6", strokeWidth: 1.5 }
          : { stroke: "#f59e0b", strokeWidth: 1.2, strokeDasharray: "5 3" },
        markerEnd: {
          type: "arrowclosed" as const,
          color: resolved ? "#3b82f6" : "#f59e0b",
          width: 12,
          height: 12,
        },
      });
    }
  }

  const columnCount = nodes.reduce(
    (sum, n) => sum + (n.data as TableNodeData).columns.length,
    0,
  );

  return {
    nodes,
    edges,
    stats: {
      tableCount: nodes.length,
      columnCount,
      mappingCount: graph.edges.length,
      drawnEdgeCount: edges.length,
      unresolvedMappingCount,
    },
  };
}

// ── Layout ──────────────────────────────────────────────────────────
//
// Tables lay out input -> Var -> output via dagre LR, sized by column
// count so tables with more rows get proportional height instead of
// dagre's fixed-box default (which would clip column handles).

export const TABLE_NODE_WIDTH = 220;
export const TABLE_HEADER_HEIGHT = 32;
export const TABLE_ROW_HEIGHT = 22;
export const TABLE_MIN_HEIGHT = 60;

export function tableNodeHeight(columnCount: number): number {
  return Math.max(
    TABLE_MIN_HEIGHT,
    TABLE_HEADER_HEIGHT + columnCount * TABLE_ROW_HEIGHT,
  );
}

export function applyComponentGraphLayout(nodes: Node[], edges: Edge[]): Node[] {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: "LR", nodesep: 30, ranksep: 100 });

  for (const node of nodes) {
    const columnCount = (node.data as TableNodeData).columns.length;
    g.setNode(node.id, {
      width: TABLE_NODE_WIDTH,
      height: tableNodeHeight(columnCount),
    });
  }
  for (const edge of edges) {
    g.setEdge(edge.source, edge.target);
  }

  dagre.layout(g);

  return nodes.map((node) => {
    const pos = g.node(node.id);
    const columnCount = (node.data as TableNodeData).columns.length;
    const height = tableNodeHeight(columnCount);
    return {
      ...node,
      position: {
        x: pos.x - TABLE_NODE_WIDTH / 2,
        y: pos.y - height / 2,
      },
    };
  });
}

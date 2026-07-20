import type { Node, Edge } from "@xyflow/react";

// ── Linear chain collapsing ──────────────────────────────────────────
//
// Some jobs are pure unbranched pipelines with 100+ components in a row
// (dagre lays these out as one endless horizontal strip - unreadable and,
// at real-world sizes, a real rendering cost). A "linear chain" is a
// maximal run of interior components where every member has exactly one
// distinct predecessor and one distinct successor (in the whole node/edge
// set passed in - control-flow and data-flow edges both count, since both
// drive dagre's rank assignment). Runs at or above `threshold` collapse
// into a single expandable summary node; the boundary components on
// either side of the run stay visible for context.

export interface ChainInfo {
  /** Stable id for the synthetic summary node. */
  id: string;
  /** Original `comp:*` node ids absorbed into this chain, in path order. */
  memberIds: string[];
  /** componentType per member, same order as memberIds. */
  memberTypes: string[];
  /** The node feeding into the chain (stays visible). */
  entryId: string;
  /** The node the chain feeds into (stays visible). */
  exitId: string;
}

export interface ChainSummaryNodeData {
  memberIds: string[];
  memberTypes: string[];
  count: number;
  entryId: string;
  exitId: string;
  dimmed?: boolean;
  highlighted?: boolean;
  [key: string]: unknown;
}

export interface ChainCollapseResult {
  nodes: Node[];
  edges: Edge[];
  chains: ChainInfo[];
}

export const DEFAULT_CHAIN_THRESHOLD = 6;
export const CHAIN_SUMMARY_NODE_TYPE = "chainSummaryNode";

/**
 * Detect and collapse maximal unbranched runs of `threshold` or more nodes.
 * `expandedChainIds` lets already-expanded chains stay fully visible (the
 * detection always runs over the full node/edge set, then any chain whose id
 * is in `expandedChainIds` is left uncollapsed).
 */
export function collapseLinearChains(
  nodes: Node[],
  edges: Edge[],
  expandedChainIds: Set<string> = new Set(),
  threshold: number = DEFAULT_CHAIN_THRESHOLD,
): ChainCollapseResult {
  if (nodes.length < threshold) {
    return { nodes, edges, chains: [] };
  }

  const nodeById = new Map(nodes.map((n) => [n.id, n]));
  const inNeighbors = new Map<string, Set<string>>();
  const outNeighbors = new Map<string, Set<string>>();
  for (const n of nodes) {
    inNeighbors.set(n.id, new Set());
    outNeighbors.set(n.id, new Set());
  }
  for (const e of edges) {
    if (!nodeById.has(e.source) || !nodeById.has(e.target)) continue;
    outNeighbors.get(e.source)!.add(e.target);
    inNeighbors.get(e.target)!.add(e.source);
  }

  const isSimple = (id: string) =>
    inNeighbors.get(id)!.size === 1 && outNeighbors.get(id)!.size === 1;

  // A "chain link" u -> v: u is simple with sole successor v, and v is
  // simple with sole predecessor u - i.e. a straight, unbranched hop.
  const nextInChain = new Map<string, string>();
  const prevInChain = new Map<string, string>();
  for (const n of nodes) {
    if (!isSimple(n.id)) continue;
    const [v] = outNeighbors.get(n.id)!;
    if (isSimple(v) && [...inNeighbors.get(v)!][0] === n.id) {
      nextInChain.set(n.id, v);
      prevInChain.set(v, n.id);
    }
  }

  const chains: ChainInfo[] = [];
  const visited = new Set<string>();

  for (const n of nodes) {
    if (visited.has(n.id)) continue;
    if (!nextInChain.has(n.id)) continue; // can't extend forward - not a run
    if (prevInChain.has(n.id)) continue; // someone in-chain leads into it - not a run start

    const run: string[] = [n.id];
    const seenThisRun = new Set([n.id]);
    let cur = n.id;
    while (nextInChain.has(cur)) {
      const next = nextInChain.get(cur)!;
      if (seenThisRun.has(next)) break; // cycle guard - never infinite-loop
      run.push(next);
      seenThisRun.add(next);
      cur = next;
    }
    run.forEach((id) => visited.add(id));

    if (run.length < threshold) continue;

    // run[0] and run[last] are both `isSimple` by construction (see above),
    // so each has exactly one distinct neighbor on its open side.
    const [entryId] = inNeighbors.get(run[0])!;
    const [exitId] = outNeighbors.get(run[run.length - 1])!;

    chains.push({
      id: `chain:${run[0]}:${run[run.length - 1]}`,
      memberIds: run,
      memberTypes: run.map((id) => String((nodeById.get(id)!.data as { componentType?: string }).componentType ?? "")),
      entryId,
      exitId,
    });
  }

  const activeChains = chains.filter((c) => !expandedChainIds.has(c.id));
  if (activeChains.length === 0) {
    return { nodes, edges, chains };
  }

  const collapsedMemberIds = new Set(activeChains.flatMap((c) => c.memberIds));
  const chainByMember = new Map<string, ChainInfo>();
  for (const c of activeChains) {
    for (const id of c.memberIds) chainByMember.set(id, c);
  }

  const remainingNodes = nodes.filter((n) => !collapsedMemberIds.has(n.id));
  const summaryNodes: Node[] = activeChains.map((c) => ({
    id: c.id,
    type: CHAIN_SUMMARY_NODE_TYPE,
    position: { x: 0, y: 0 },
    data: {
      memberIds: c.memberIds,
      memberTypes: c.memberTypes,
      count: c.memberIds.length,
      entryId: c.entryId,
      exitId: c.exitId,
    } satisfies ChainSummaryNodeData,
  }));

  const newEdges: Edge[] = [];
  for (const e of edges) {
    const srcChain = chainByMember.get(e.source);
    const tgtChain = chainByMember.get(e.target);
    if (srcChain && tgtChain) continue; // fully interior - both ends absorbed
    if (srcChain) {
      newEdges.push({ ...e, id: `${e.id}:collapsed`, source: srcChain.id });
      continue;
    }
    if (tgtChain) {
      newEdges.push({ ...e, id: `${e.id}:collapsed`, target: tgtChain.id });
      continue;
    }
    newEdges.push(e);
  }

  return { nodes: [...remainingNodes, ...summaryNodes], edges: newEdges, chains };
}

import { describe, it } from "vitest";
import { readFileSync } from "fs";
import path from "path";
import dagre from "@dagrejs/dagre";
import {
  manifestToCollapsedFlow,
  applyConnectionVisibility,
  getDimensionsForId,
  type ManifestData,
} from "@/lib/graphTransform";

const scanDir = path.resolve(__dirname, "../../../..", "knowledge_base/intodoc-output/vaxinova/scan");
const manifest = JSON.parse(readFileSync(path.join(scanDir, "manifest.json"), "utf-8")) as ManifestData;
const connections = JSON.parse(readFileSync(path.join(scanDir, "connections.json"), "utf-8"));

describe("repro dagre internals", () => {
  it("inspects raw dagre output", () => {
    const flow = manifestToCollapsedFlow(manifest, connections, undefined, new Set(["Global"]));
    const v = applyConnectionVisibility(flow.nodes, flow.edges, true);
    const groupMembership = new Map<string, string>();
    for (const [job, group] of Object.entries(manifest.job_groups || {})) {
      groupMembership.set(`job:${job}`, group);
    }

    const g = new dagre.graphlib.Graph({ compound: true });
    g.setDefaultEdgeLabel(() => ({}));
    g.setGraph({ rankdir: "LR", nodesep: 48, ranksep: 96 });

    const activeGroups = new Set<string>();
    for (const node of v.nodes) {
      const gr = groupMembership.get(node.id);
      if (gr) activeGroups.add(gr);
    }
    for (const groupId of activeGroups) g.setNode(`cluster:${groupId}`, {});

    for (const node of v.nodes) {
      const { width, height } = getDimensionsForId(node.id, true);
      g.setNode(node.id, { width, height });
      const parentGroup = groupMembership.get(node.id);
      if (parentGroup && activeGroups.has(parentGroup)) {
        g.setParent(node.id, `cluster:${parentGroup}`);
      }
    }
    for (const edge of v.edges) {
      if (g.hasNode(edge.source) && g.hasNode(edge.target)) g.setEdge(edge.source, edge.target);
    }

    dagre.layout(g);

    const cluster = g.node("cluster:Global");
    console.log("cluster:Global ->", JSON.stringify(cluster));
    // Raw child positions (pre-deflatten)
    const jobs = v.nodes.filter((n) => n.id.startsWith("job:"));
    const ys = jobs.map((n) => Math.round(g.node(n.id).y));
    const xs = jobs.map((n) => Math.round(g.node(n.id).x));
    console.log("jobs raw x range:", Math.min(...xs), "-", Math.max(...xs));
    console.log("jobs raw y range:", Math.min(...ys), "-", Math.max(...ys));
    for (const id of ["group:BRA", "conn:connection_BRA_VBR_MSSQL"]) {
      const p = g.node(id);
      console.log(`${id} raw ->`, JSON.stringify({ x: Math.round(p.x), y: Math.round(p.y), w: p.width, h: p.height }));
    }
    // What does dagre think the graph size is?
    console.log("graph:", JSON.stringify(g.graph()));
  });
});

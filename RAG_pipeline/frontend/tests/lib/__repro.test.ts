import { describe, it } from "vitest";
import { readFileSync } from "fs";
import path from "path";
import {
  manifestToCollapsedFlow,
  applyCompoundDagreLayout,
  applyConnectionVisibility,
  getDimensionsForId,
  type ManifestData,
} from "@/lib/graphTransform";

const scanDir = path.resolve(__dirname, "../../../..", "knowledge_base/intodoc-output/vaxinova/scan");
const manifest = JSON.parse(readFileSync(path.join(scanDir, "manifest.json"), "utf-8")) as ManifestData;
const connections = JSON.parse(readFileSync(path.join(scanDir, "connections.json"), "utf-8"));

function report(expandedGroup: string) {
  const flow = manifestToCollapsedFlow(manifest, connections, undefined, new Set([expandedGroup]));
  const v = applyConnectionVisibility(flow.nodes, flow.edges, true);
  const groupMembership = new Map<string, string>();
  for (const [job, group] of Object.entries(manifest.job_groups || {})) {
    groupMembership.set(`job:${job}`, group);
  }
  const laid = applyCompoundDagreLayout(v.nodes, v.edges, "LR", true, groupMembership);

  const bounds = (ids: string[]) => {
    const ns = laid.filter((n) => ids.includes(n.id));
    if (ns.length === 0) return null;
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const n of ns) {
      const { width, height } = getDimensionsForId(n.id, true);
      minX = Math.min(minX, n.position.x);
      maxX = Math.max(maxX, n.position.x + width);
      minY = Math.min(minY, n.position.y);
      maxY = Math.max(maxY, n.position.y + height);
    }
    return { minX: Math.round(minX), maxX: Math.round(maxX), minY: Math.round(minY), maxY: Math.round(maxY) };
  };

  console.log(`\n=== expanded: ${expandedGroup} (${v.nodes.length} nodes, ${v.edges.length} edges) ===`);
  const expandedJobs = laid.filter((n) => n.id.startsWith("job:")).map((n) => n.id);
  console.log("expanded jobs bounds:", bounds(expandedJobs));
  for (const n of laid.filter((n) => n.id.startsWith("group:"))) {
    console.log(`  ${n.id}: x=${Math.round(n.position.x)} y=${Math.round(n.position.y)}`);
  }
  for (const n of laid.filter((n) => n.id.startsWith("conn:"))) {
    console.log(`  ${n.id}: x=${Math.round(n.position.x)} y=${Math.round(n.position.y)}`);
  }
  const all = laid.map((n) => n.id);
  console.log("canvas bounds:", bounds(all));
}

describe("repro vaxinova geometry", () => {
  it("reports layout geometry", () => {
    report("Global");
  });
});

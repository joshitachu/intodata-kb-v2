import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { BusinessTopologySchema } from "@/types/landscape";

/**
 * Parses every curated topology overlay present on this machine against the
 * browser contract.
 *
 * `BusinessTopologySchema` is `.strict()`, so ONE field a producer emits and
 * this schema does not declare rejects the whole document and the canvas
 * renders empty -- with no error a user can see. Unit fixtures cannot catch
 * that, because a fixture is written against the schema it is testing. A real
 * published artifact is the only input that can disagree.
 *
 * Curated overlays live under gitignored client paths, so this suite skips
 * when none is present. It deliberately discovers them instead of naming a
 * project, so no client name enters tracked code.
 */
const OUTPUT_ROOT = join(__dirname, "..", "..", "..", "..", "knowledge_base", "output");

function curatedTopologyPaths(): string[] {
  if (!existsSync(OUTPUT_ROOT)) return [];
  const found: string[] = [];
  for (const project of readdirSync(OUTPUT_ROOT, { withFileTypes: true })) {
    if (!project.isDirectory()) continue;
    for (const candidate of [
      join(OUTPUT_ROOT, project.name, "topology", "business_topology.curated.json"),
      join(OUTPUT_ROOT, project.name, "docs", "_topology", "business_topology.curated.json"),
    ]) {
      if (existsSync(candidate)) found.push(candidate);
    }
  }
  return found.sort();
}

const paths = curatedTopologyPaths();

describe.skipIf(paths.length === 0)("curated topology overlays parse against the browser contract", () => {
  it.each(paths.map((path) => [path.slice(OUTPUT_ROOT.length + 1), path]))(
    "%s",
    (_label, path) => {
      const raw = JSON.parse(readFileSync(path, "utf-8"));
      const parsed = BusinessTopologySchema.parse(raw);

      // Strict parsing silently drops nothing, but a schema-shaped object with
      // every collection empty would pass and render nothing, so assert the
      // document actually carries a landscape.
      expect(parsed.systems.length).toBeGreaterThan(0);
      expect(parsed.data_streams.length).toBeGreaterThan(0);
      expect(parsed.scan_fingerprint.length).toBeGreaterThan(0);
    },
  );

  it("preserves the additive slots wherever a producer filled them", () => {
    const enriched = paths
      .map((path) => BusinessTopologySchema.parse(JSON.parse(readFileSync(path, "utf-8"))))
      .find((topology) => (topology.business_rules ?? []).length > 0);
    // Only an enriched overlay exercises this; the others are the legacy shape
    // and are covered by the parse cases above.
    if (!enriched) return;

    const rule = (enriched.business_rules ?? [])[0];
    expect(rule.statement.length).toBeGreaterThan(0);
    expect(rule.stream_ids.length).toBeGreaterThan(0);

    const withWidth = enriched.data_streams.find((stream) => stream.contract_width);
    expect(withWidth?.contract_width).toBeTruthy();

    const valueDomain = enriched.transformations.find(
      (transformation) => transformation.kind === "value_domain",
    );
    // A value domain must name the field it constrains, or a renderer cannot
    // nest it under that field and it reads as a standalone business decision.
    expect(valueDomain?.target_field).toBeTruthy();

    const observation = (enriched.runtime_observations ?? [])[0];
    expect(observation?.metrics?.length ?? 0).toBeGreaterThan(0);
    for (const metric of observation?.metrics ?? []) {
      // A measurement without its window and source is not reportable.
      expect(metric.window.length).toBeGreaterThan(0);
      expect(metric.source.length).toBeGreaterThan(0);
    }
  });
});

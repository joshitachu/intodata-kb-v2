import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import type { ZodRawShape } from "zod";
import {
  ArtifactAnnotationSchema,
  BusinessConflictSchema,
  BusinessControlSchema,
  BusinessDataStreamSchema,
  BusinessDeliveryPathSchema,
  BusinessDeliveryPathSegmentSchema,
  BusinessIntegrationSolutionSchema,
  BusinessObjectSchema,
  BusinessRiskSchema,
  BusinessRuleSchema,
  BusinessSystemSchema,
  BusinessTopologySchema,
  BusinessTransformationSchema,
  BusinessUnknownSchema,
  ComponentAnnotationSchema,
  ContractWidthSchema,
  DeploymentAgreementSchema,
  LineageAnnotationSchema,
  RuntimeMetricSchema,
  RuntimeObservationSchema,
  StreamConditionSchema,
  TransformationMappingSchema,
} from "@/types/landscape";

/**
 * Field-for-field parity between the MCP pydantic contract and this strict zod
 * mirror, in BOTH directions.
 *
 * - A contract field the mirror omits rejects the whole topology the moment a
 *   producer emits it (`.strict()`), and the canvas renders empty.
 * - A mirror field the contract lacks is an invention the producer can never
 *   fill.
 *
 * The contract is read as source text rather than imported (different
 * runtimes). The suite skips on a split checkout where it is absent.
 */
const CONTRACT = join(
  __dirname, "..", "..", "..", "..",
  "knowledge_base", "mcp-server", "src", "mcp_server", "services", "business_topology", "models.py",
);

function contractFields(source: string): Map<string, Set<string>> {
  const own = new Map<string, Set<string>>();
  const bases = new Map<string, string[]>();
  let current: string | null = null;
  let inDocstring = false;
  for (const line of source.split("\n")) {
    const classMatch = /^class (\w+)\(([^)]*)\):/.exec(line);
    if (classMatch) {
      current = classMatch[1];
      own.set(current, new Set());
      bases.set(current, classMatch[2].split(",").map((item) => item.trim()).filter(Boolean));
      inDocstring = false;
      continue;
    }
    if (current === null) continue;
    if (/^\S/.test(line)) {
      current = null;
      continue;
    }
    const quotes = (line.match(/"""/g) ?? []).length;
    if (inDocstring) {
      if (quotes % 2 === 1) inDocstring = false;
      continue;
    }
    if (quotes % 2 === 1) {
      inDocstring = true;
      continue;
    }
    const field = /^ {4}(\w+)\s*:/.exec(line);
    if (field && field[1] !== "model_config") own.get(current)!.add(field[1]);
  }
  const resolved = (name: string): Set<string> => {
    const fields = new Set(own.get(name) ?? []);
    for (const base of bases.get(name) ?? []) for (const item of resolved(base)) fields.add(item);
    return fields;
  };
  return new Map([...own.keys()].map((name) => [name, resolved(name)]));
}

const topologyShape = BusinessTopologySchema.shape;

/** MCP class name -> the zod shape that mirrors it. */
const MIRRORS: Record<string, ZodRawShape> = {
  BusinessTopologyMetadata: topologyShape.metadata.shape,
  BusinessSystem: BusinessSystemSchema.shape,
  IntegrationSolution: BusinessIntegrationSolutionSchema.shape,
  BusinessObject: BusinessObjectSchema.shape,
  DeliverySegment: BusinessDeliveryPathSegmentSchema.shape,
  DeliveryPath: BusinessDeliveryPathSchema.shape,
  ContractWidth: ContractWidthSchema.shape,
  StreamCondition: StreamConditionSchema.shape,
  DeploymentAgreement: DeploymentAgreementSchema.shape,
  BusinessDataStream: BusinessDataStreamSchema.shape,
  TransformationMapping: TransformationMappingSchema.shape,
  BusinessTransformation: BusinessTransformationSchema.shape,
  BusinessRule: BusinessRuleSchema.shape,
  BusinessControl: BusinessControlSchema.shape,
  BusinessRisk: BusinessRiskSchema.shape,
  BusinessUnknown: BusinessUnknownSchema.shape,
  RuntimeMetric: RuntimeMetricSchema.shape,
  RuntimeObservation: RuntimeObservationSchema.shape,
  BusinessConflict: BusinessConflictSchema.shape,
  ArtifactAnnotation: ArtifactAnnotationSchema.shape,
  ComponentAnnotation: ComponentAnnotationSchema.shape,
  LineageAnnotation: LineageAnnotationSchema.shape,
  BusinessTopologyCoverage: topologyShape.coverage.shape,
  BusinessTopology: topologyShape,
};

/** Abstract bases with no serialized shape of their own. */
const ABSTRACT = new Set(["StrictModel", "EvidenceBackedClaim"]);

describe.skipIf(!existsSync(CONTRACT))("business topology zod mirror matches the MCP contract", () => {
  const contract = existsSync(CONTRACT) ? contractFields(readFileSync(CONTRACT, "utf-8")) : new Map();

  it("maps every contract class", () => {
    const unmapped = [...contract.keys()].filter((name) => !ABSTRACT.has(name) && !(name in MIRRORS));
    expect(unmapped).toEqual([]);
  });

  it.each(Object.keys(MIRRORS))("%s declares exactly the contract's fields", (name) => {
    const expected = [...(contract.get(name) ?? [])].sort();
    const declared = Object.keys(MIRRORS[name]).sort();
    expect(expected.length).toBeGreaterThan(0);
    expect(declared).toEqual(expected);
  });
});
